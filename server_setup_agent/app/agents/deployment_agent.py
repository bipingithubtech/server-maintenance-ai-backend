"""
DeploymentAgent — Two-phase pipeline.

Phase 1 (LLM): One single LLM call that extracts all required deployment
               info from the user query. If anything is missing it asks.
               Result is saved in a DeploymentContext dataclass.

Phase 2 (Direct): All steps run as plain Python using the saved context.
                  No LLM, no history, no rate limits.

Required info collected in Phase 1:
  - github_url  : full GitHub repo URL
  - stack       : react | vite | angular | nextjs | nodejs | nestjs | fastapi | flask | django
  - port        : port the app listens on (nginx proxies to this)
  - domain      : domain or IP for nginx
  - env_vars    : dict of KEY=value pairs (empty if not needed)
"""

import re
import json
import base64
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from loguru import logger

from app.services.llm_service import get_llm
from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.package_tool import PackageTool
from app.tools.nginx_tool import NginxTool
from app.tools.systemd_tool import SystemdTool
from app.tools.pm2_tool import PM2Tool
from app.services.teams_alert_service import TeamsAlerter

# Absolute path to deployment_context.json — always lands in server_setup_agent/
# regardless of what cwd the server was launched from.
_DEPLOYMENT_CONTEXT_FILE = Path(__file__).resolve().parent.parent.parent / "deployment_context.json"


# ── Deployment context ─────────────────────────────────────────────────────────

@dataclass
class DeploymentContext:
    github_url       : str
    stack            : str
    port             : str
    domain           : str
    env_vars         : Dict[str, str] = field(default_factory=dict)
    process_manager  : str = ""      # pm2 | systemd | docker
    branch           : str = "main"  # branch to clone
    app_type         : str = ""      # frontend | backend

    # Derived — filled automatically
    app_name   : str = ""
    app_path   : str = ""

    # Stacks that are always frontend (served as static files)
    FRONTEND_STACKS = frozenset({"react", "vite", "angular"})
    # Stacks that are always backend (run as a process)
    BACKEND_STACKS  = frozenset({"fastapi", "flask", "django", "nodejs", "nestjs", "nextjs", "ai"})

    def __post_init__(self):
        if not self.github_url.endswith(".git"):
            self.github_url += ".git"
        repo = self.github_url.rstrip("/").split("/")[-1].replace(".git", "")
        self.app_name = repo.lower().replace("_", "-")
        # app_path is set later once app_type is known — see _resolve_app_path()
        if not self.app_path:
            self._resolve_app_path()

    def _resolve_app_path(self) -> None:
        """Default deploy path. Always absolute — never $HOME."""
        if self.app_type == "frontend":
            self.app_path = f"/opt/ui/{self.app_name}"
        elif self.app_type == "backend":
            self.app_path = f"/opt/api/{self.app_name}"
        else:
            self.app_path = f"/opt/{self.app_name}"



_GATHER_SYSTEM = """You are a deployment assistant. 
Your ONLY job is to extract deployment info from the user message and return it as JSON.

Required fields:
  github_url  - full GitHub HTTPS URL (must start with https://github.com/)
  stack       - one of: react, vite, angular, nextjs, nodejs, nestjs, fastapi, flask, django
                IMPORTANT: if user says "next js", "nextjs", or "next.js" → use "nextjs".
                If user says "nestjs" or "nest js" → use "nestjs".
                If user says "next js frontend" → use "nextjs". Do NOT ask for clarification.
  port        - the internal port the app process listens on (NOT 80 or 443).

Optional fields (do NOT ask for these — fill automatically or leave empty):
  domain      - domain name or IP for nginx server_name. If not provided, return "".
  env_vars    - object with KEY: "value" pairs if needed (empty object {} if not needed)
  process_manager - deployment method: pm2 | systemd | docker. Default is pm2 unless user specifies.

Rules:
- If github_url, stack, AND port are ALL present in the combined message, return JSON immediately.
- Only list a field as missing if it is truly absent from the message.
- NEVER list domain as missing — it is optional.
- NEVER ask for clarification about nextjs vs nestjs — if user says "next js" use "nextjs".
- Return ONLY valid JSON. No markdown, no explanation.

SPECIAL: For server-maintenance-ai-backend repo (FastAPI app), use process_manager: "docker" by default.

Examples:

User: "deploy https://github.com/Org/repo.git stack nextjs port 3000"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"nextjs","port":"3000","domain":"","env_vars":{},"process_manager":"pm2"}

User: "deploy https://github.com/Org/repo.git port 8000 stack next js for frontend"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"nextjs","port":"8000","domain":"","env_vars":{},"process_manager":"pm2"}

User: "deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000"
Response: {"github_url":"https://github.com/bipingithubtech/server-maintenance-ai-backend.git","stack":"fastapi","port":"8000","domain":"","env_vars":{},"process_manager":"docker"}

User: "deploy https://github.com/Org/repo.git stack fastapi port 8000 domain myapp.com env DATABASE_URL=postgres://localhost/db"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"fastapi","port":"8000","domain":"myapp.com","env_vars":{"DATABASE_URL":"postgres://localhost/db"},"process_manager":"pm2"}
"""


# ── Agent ──────────────────────────────────────────────────────────────────────

class DeploymentAgent:

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}
        self.executor_type   = executor_type  # Store for later use
        self.executor_config = executor_config  # Store for later use
        self.executor = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.linux    = LinuxTool(self.executor)
        self.pkg      = PackageTool(self.executor)
        self.nginx    = NginxTool(self.executor)
        self.systemd  = SystemdTool(self.executor)
        self.pm2      = PM2Tool(self.executor)
        self.llm      = get_llm()
        self.alerter  = TeamsAlerter()
        self._server  = executor_config.get("host", "unknown")

    # ── Branch fetch ──────────────────────────────────────────────────────────

    def _is_private_repo(self, github_url: str) -> bool:
        """
        Returns True if the repo is private (unauthenticated API returns 404).
        Returns False if public or if the check fails (assume public, try anyway).
        """
        import urllib.request as _req, json as _json
        try:
            clean = github_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            parts = clean.split("/")
            if len(parts) < 2:
                return False
            owner, repo = parts[0], parts[1]
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            req = _req.Request(api_url, headers={
                "User-Agent": "server-setup-agent",
                "Accept": "application/vnd.github.v3+json",
            })
            with _req.urlopen(req, timeout=6) as resp:
                data = _json.loads(resp.read().decode())
                return data.get("private", False)
        except Exception as e:
            # 404 = private (unauthenticated), other errors = assume public
            return "404" in str(e) or "HTTP Error 404" in str(e)

    def _try_read_server_token(self) -> str:
        """
        Attempt to read a GitHub token from ~/.github_token on the remote server.
        Returns the token string or empty string.
        """
        try:
            _, out, _ = self.executor.execute("cat ~/.github_token 2>/dev/null")
            token = out.strip()
            if token and not token.startswith("cat:") and len(token) > 10:
                logger.info("[DEPLOY] Found GitHub token from ~/.github_token on server")
                return token
        except Exception:
            pass
        return ""

    def _validate_github_token(self, token: str, repo_url: str) -> bool:
        """
        Validate GitHub token by attempting a minimal API call.
        Returns True if token is valid, False otherwise.
        """
        import urllib.request as _req, json as _json
        try:
            # Try to fetch user info with the token
            req = _req.Request(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "User-Agent": "server-setup-agent",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with _req.urlopen(req, timeout=6) as resp:
                data = _json.loads(resp.read().decode())
                if "login" in data:
                    logger.info(f"[DEPLOY] GitHub token validated for user: {data['login']}")
                    return True
        except Exception as e:
            logger.warning(f"[DEPLOY] GitHub token validation failed: {str(e)[:100]}")
        return False

    def _fetch_branches(self, github_url: str) -> list:
        """
        Fetch available branches from GitHub API.
        Works for public repos without auth.
        Returns list of branch names, or [] on failure.
        """
        import urllib.request, json as _json
        try:
            # Parse owner/repo from URL
            clean = github_url.replace("https://github.com/", "").replace(".git", "").strip("/")
            parts = clean.split("/")
            if len(parts) < 2:
                return []
            owner, repo = parts[0], parts[1]
            api_url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=50"
            req = urllib.request.Request(api_url, headers={
                "User-Agent": "server-setup-agent",
                "Accept": "application/vnd.github.v3+json",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode())
                return [b["name"] for b in data]
        except Exception:
            return []

    # ── Phase 1: gather context via LLM ───────────────────────────────────────

    def _gather_context(self, query: str) -> DeploymentContext:
        """
        Single LLM call to extract all required deployment info.
        In API mode: raises NeedsInputError instead of calling input().
        In CLI mode: falls back to input() prompts.
        """
        from app.services.conversation_service import NeedsInputError
        pf       = getattr(self, '_prefill', None) or {}
        api_mode = True  # always API mode — CLI uses server_cli.py directly

        # ── REDEPLOY DETECTION ─────────────────────────────────────────────
        # Check if this is a redeploy request (pull latest from current branch)
        if self._is_redeploy_query(query):
            logger.info("[DEPLOY] Redeploy request detected")
            
            # Check if deployment_context.json exists to know where the app is deployed
            if _DEPLOYMENT_CONTEXT_FILE.exists():
                try:
                    with open(_DEPLOYMENT_CONTEXT_FILE, "r") as f:
                        last_deploy = json.load(f)
                    
                    app_path = last_deploy.get("app_path", "")
                    app_name = last_deploy.get("app_name", "")
                    github_url = last_deploy.get("github_url", "")
                    stack = last_deploy.get("stack", "")
                    port = last_deploy.get("port", "")
                    domain = last_deploy.get("domain", "")
                    process_manager = last_deploy.get("process_manager", "")
                    
                    if app_path and self._dir_exists(app_path):
                        # Get the current branch deployed
                        current_branch = self._get_current_git_branch(app_path)
                        
                        logger.info(f"[REDEPLOY] Current branch: {current_branch} at {app_path}")
                        
                        # Ask user: use this branch or provide a different one?
                        raise NeedsInputError(
                            f"📦 **Redeploy**\n\n"
                            f"App: `{app_name}`\n"
                            f"Current Branch: `{current_branch}`\n\n"
                            f"Use this branch? Type:\n"
                            f"  - **yes** to redeploy from `{current_branch}`\n"
                            f"  - Branch name to redeploy from a different branch (e.g., 'develop', 'staging')",
                            {
                                "step": "choose_redeploy_branch",
                                "app_path": app_path,
                                "app_name": app_name,
                                "current_branch": current_branch,
                                "ctx_partial": {
                                    "github_url": github_url,
                                    "stack": stack,
                                    "port": port,
                                    "domain": domain,
                                    "process_manager": process_manager,
                                    "branch": current_branch,
                                    "app_name": app_name,
                                    "app_path": app_path,
                                },
                                "prefill": pf,
                                "agent": "deployment",
                            }
                        )
                except NeedsInputError:
                    # Re-raise NeedsInputError (don't catch it as generic exception)
                    raise
                except json.JSONDecodeError:
                    logger.warning("[REDEPLOY] deployment_context.json is invalid — treating as new deployment")
                except Exception as e:
                    logger.warning(f"[REDEPLOY] Could not read deployment context: {e} — treating as new deployment")

        # ── REDEPLOY FAST-PATH: Skip all questions if redeploy is confirmed ────
        # If user already chose branch (from previous turn), build context and return immediately
        if pf.get("redeploy") and pf.get("current_branch"):
            logger.info(f"[REDEPLOY FAST-PATH] Branch chosen: {pf.get('current_branch')}")
            logger.info(f"[REDEPLOY FAST-PATH] Using app_path: {pf.get('app_path')}, github_url: {pf.get('github_url')}")
            ctx = DeploymentContext(
                github_url = pf.get("github_url", "https://github.com/x/x.git"),
                stack      = pf.get("stack", ""),
                port       = str(pf.get("port", "")),
                domain     = pf.get("domain", "_"),
                env_vars   = pf.get("env_vars", {}),
                process_manager = pf.get("process_manager", ""),
                branch     = pf.get("current_branch", "main"),
                app_type   = pf.get("app_type", ""),
            )
            # Restore derived fields directly
            ctx.app_name = pf.get("app_name", ctx.app_name)
            ctx.app_path = pf.get("app_path", ctx.app_path)
            logger.info(f"[REDEPLOY FAST-PATH] Returning context: app={ctx.app_name}, path={ctx.app_path}, branch={ctx.branch}")
            return ctx

        # ── Early exit: all core fields already in prefill from a prior conversation turn ──
        # This happens after need_process_manager / need_branch / need_github_token steps.
        # Skip the LLM entirely and go straight to the post-LLM checks.
        if pf.get("github_url") and pf.get("stack") and pf.get("port"):
            logger.info("[DEPLOY] Core fields already in prefill — skipping LLM gather")
            server_host = (
                self._server
                if self._server and self._server not in ("unknown", "None", None, "127.0.0.1", "localhost")
                else ""
            )
            if not server_host:
                executor_host = getattr(self.executor, 'host', None) or getattr(self.executor, 'hostname', None)
                if executor_host and executor_host not in ("127.0.0.1", "localhost", None):
                    server_host = executor_host
            ctx = DeploymentContext(
                github_url = pf["github_url"],
                stack      = pf["stack"].lower(),
                port       = str(pf["port"]),
                domain     = pf.get("domain") or server_host or "_",
                env_vars   = pf.get("env_vars", {}),
            )
            # Apply already-collected answers
            if pf.get("app_type"):
                ctx.app_type = pf["app_type"]
                ctx._resolve_app_path()
            if pf.get("clone_dir"):
                ctx.app_path = pf["clone_dir"].rstrip("/")
            if pf.get("process_manager"):
                ctx.process_manager = pf["process_manager"]
            if pf.get("branch"):
                ctx.branch = pf["branch"]
            # Fall through to the post-LLM checks below (process_manager, branch, token, env)
            return self._complete_context(ctx, pf, query)
        # Auto-fill domain from the connected server's host if not already in prefill
        # Check both _server attr and executor config — host may be None for local executors
        server_host = (
            self._server
            if self._server and self._server not in ("unknown", "None", None, "127.0.0.1", "localhost")
            else ""
        )
        # For local executor the host is the actual server IP from the SSH connection config
        if not server_host:
            executor_host = getattr(self.executor, 'host', None) or getattr(self.executor, 'hostname', None)
            if executor_host and executor_host not in ("127.0.0.1", "localhost", None):
                server_host = executor_host
        if server_host and not pf.get("domain"):
            pf["domain"] = server_host

        # Inject server host into system prompt so LLM knows domain is pre-filled
        gather_system = _GATHER_SYSTEM
        if server_host:
            gather_system = _GATHER_SYSTEM + (
                f"\n\nNOTE: The user is already connected to server '{server_host}'. "
                f"Use '{server_host}' as the domain value automatically — "
                f"do NOT list domain as missing."
            )

        messages = [
            SystemMessage(content=gather_system),
            HumanMessage(content=query),
        ]

        while True:
            time.sleep(1)
            response = self.llm.invoke(messages)
            raw = response.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                messages.append(response)
                messages.append(HumanMessage(content="Please respond with valid JSON only."))
                continue

            # Missing required fields — ask frontend
            if "missing" in data:
                question = data.get("question", "Please provide the missing deployment information.")
                raise NeedsInputError(question, {
                    "step": "gather", "messages": messages,
                    "query": query, "prefill": pf, "agent": "deployment",
                })

            # All fields present — build context and complete
            ctx = DeploymentContext(
                github_url = data["github_url"],
                stack      = data["stack"].lower(),
                port       = str(data["port"]),
                domain     = data.get("domain") or pf.get("domain") or server_host or "_",
                env_vars   = data.get("env_vars", {}),
                process_manager = data.get("process_manager", ""),  # Can be pm2, systemd, docker, or empty
            )
            return self._complete_context(ctx, pf, query)

    def _complete_context(self, ctx: "DeploymentContext", pf: dict, query: str) -> "DeploymentContext":
        """
        Handles all post-LLM steps: process_manager, branch, github_token, env.
        Called from both the LLM path and the early-exit (prefill) path.
        Each NeedsInputError stores the full ctx_partial so the resume path
        can skip the LLM entirely on the next turn.
        """
        from app.services.conversation_service import NeedsInputError

        # ── Infer app_type and resolve canonical clone path FIRST ─────────
        # Must happen before process_manager question so ctx_partial captures
        # the correct /opt/ui or /opt/api path, not the bare /opt/{app_name}.
        if not ctx.app_type:
            if ctx.stack in DeploymentContext.FRONTEND_STACKS or ctx.stack == "nextjs":
                ctx.app_type = "frontend"
            elif ctx.stack in DeploymentContext.BACKEND_STACKS:
                ctx.app_type = "backend"

        # ── Deployment path ───────────────────────────────────────────
        # For redeploy, skip this question - app_path is already set
        if pf.get("clone_dir"):
            ctx.app_path = pf["clone_dir"].rstrip("/")
        elif pf.get("redeploy"):
            # Redeploy: app_path already set from deployment_context.json
            logger.info(f"[REDEPLOY] Using existing app_path: {ctx.app_path}")
        else:
            ctx._resolve_app_path()
            default_path = ctx.app_path
            logger.info(f"[DEPLOY] Auto-selected clone_dir: {default_path}")
            
            # Ask user if they want single app (direct path) or multi-app (subdirectory)
            raise NeedsInputError(
                f"How should the app be deployed?\n\n"
                f"Option 1 (Direct): Clone into single path\n"
                f"  Path: {default_path}\n"
                f"  Use when: This is the only app in this directory\n\n"
                f"Option 2 (Subdirectory): Clone into app-specific subdirectory\n"
                f"  Path: {default_path}/{ctx.app_name}\n"
                f"  Use when: Multiple apps share the same directory\n\n"
                f"Enter choice (1 or 2, or custom path):",
                {"step": "need_deploy_mode", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port, "domain": ctx.domain,
                    "app_type": ctx.app_type,
                }, "default_path": default_path, "app_name": ctx.app_name, "prefill": pf, "agent": "deployment"}
            )

        # ── Process manager ────────────────────────────────────────────
        stack = ctx.stack
        if ctx.process_manager and ctx.process_manager in ("pm2", "systemd", "docker"):
            # Already set from LLM or prefill — skip prompt
            logger.info(f"[DEPLOY] Process manager set to: {ctx.process_manager}")
        elif pf.get("process_manager"):
            ctx.process_manager = pf["process_manager"]
        elif pf.get("redeploy"):
            # Redeploy: use same process manager as before (already in ctx from deployment_context.json)
            logger.info(f"[REDEPLOY] Using existing process manager: {ctx.process_manager}")
        else:
            raise NeedsInputError(
                "Which process manager should be used to run the app?\n"
                "Options: 1. pm2  2. systemd  3. docker\n"
                "(default: pm2)",
                {"step": "need_process_manager", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port, "domain": ctx.domain,
                    "app_type": ctx.app_type,
                    "clone_dir": ctx.app_path,
                }, "prefill": pf, "agent": "deployment"}
            )

        # ── Branch selection ───────────────────────────────────────────
        if pf.get("branch"):
            ctx.branch = pf["branch"]
        elif pf.get("redeploy"):
            # Redeploy: branch already set from user choice in choose_redeploy_branch step
            logger.info(f"[REDEPLOY] Using selected branch: {ctx.branch}")
        else:
            branches = self._fetch_branches(ctx.github_url)
            branch_list = ", ".join(f"{i+1}. {b}" for i, b in enumerate(branches)) if branches else ""
            question = (
                f"Which branch to deploy?\nAvailable branches: {branch_list}\n"
                f"Enter branch name or number (default: {branches[0] if branches else 'main'}):"
            ) if branches else "Enter branch name to deploy (default: main):"
            raise NeedsInputError(
                question,
                {"step": "need_branch", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port, "domain": ctx.domain,
                    "process_manager": ctx.process_manager,
                    "app_type": ctx.app_type,
                    "clone_dir": ctx.app_path if ctx.app_path else None,
                }, "branches": branches, "prefill": pf, "agent": "deployment"}
            )

        # ── GitHub token (private repos) ───────────────────────────────
        from app.core.config import settings as _settings
        github_token = (
            pf.get("github_token")
            or getattr(self, '_github_token', None)
            or _settings.GITHUB_TOKEN
        )
        if not github_token:
            result = self._try_read_server_token()
            github_token = result

        if not github_token:
            is_private = self._is_private_repo(ctx.github_url)
            if is_private:
                raise NeedsInputError(
                    f"The repo '{ctx.github_url}' appears to be private. "
                    f"Please provide a GitHub Personal Access Token (PAT) with repo read access.\n"
                    f"Create one at: https://github.com/settings/tokens/new?scopes=repo",
                    {"step": "need_github_token", "ctx_partial": {
                        "github_url": ctx.github_url, "stack": ctx.stack,
                        "port": ctx.port, "domain": ctx.domain,
                        "process_manager": ctx.process_manager,
                        "branch": ctx.branch,
                        "app_type": ctx.app_type,
                    }, "prefill": pf, "agent": "deployment"}
                )
        else:
            # Validate the token before using it
            is_valid = self._validate_github_token(github_token, ctx.github_url)
            if not is_valid:
                raise NeedsInputError(
                    f"The GitHub token appears to be invalid or expired.\n"
                    f"Error: Authentication failed.\n\n"
                    f"Please provide a valid GitHub Personal Access Token (PAT) with repo read access:\n"
                    f"1. Create one at: https://github.com/settings/tokens/new?scopes=repo\n"
                    f"2. Make sure the token has not expired\n"
                    f"3. If using an org token, ensure it has access to this repo",
                    {"step": "need_github_token", "ctx_partial": {
                        "github_url": ctx.github_url, "stack": ctx.stack,
                        "port": ctx.port, "domain": ctx.domain,
                        "process_manager": ctx.process_manager,
                        "branch": ctx.branch,
                        "app_type": ctx.app_type,
                    }, "prefill": pf, "agent": "deployment"}
                )
            self._github_token = github_token

        # ── .env vars ──────────────────────────────────────────────────
        if pf.get("env_vars") is not None:
            ctx.env_vars = pf["env_vars"]
        elif pf.get("redeploy"):
            # Redeploy: keep existing env vars (don't ask again)
            logger.info(f"[REDEPLOY] Keeping existing .env vars")
            ctx.env_vars = {}
        else:
            env_example_vars = []
            try:
                import urllib.request as _req
                parts = ctx.github_url.replace("https://github.com/", "").replace(".git", "").split("/")
                raw_url = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/HEAD/.env.example"
                req = _req.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
                with _req.urlopen(req, timeout=5) as resp:
                    for line in resp.read().decode().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            env_example_vars.append(line.split("=")[0].strip())
            except Exception:
                pass

            env_hint = f"Required variables from .env.example: {', '.join(env_example_vars)}" if env_example_vars else ""
            raise NeedsInputError(
                f"Does this app need a .env file? If yes, provide the variables as JSON like: "
                f'{{\"DATABASE_URL\": \"postgres://...\", \"SECRET_KEY\": \"abc\"}}. '
                f'If no .env needed, reply: no\n{env_hint}',
                {"step": "need_env", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port, "domain": ctx.domain,
                    "process_manager": ctx.process_manager,
                    "branch": ctx.branch,
                    "app_type": ctx.app_type,
                }, "env_example_vars": env_example_vars, "prefill": pf, "agent": "deployment"}
            )

        # ── Domain for Nginx (ask explicitly) ──────────────────────────
        if pf.get("domain") and pf["domain"] not in ("_", "", "none"):
            # User provided explicit domain
            ctx.domain = pf["domain"]
        else:
            # Ask user for domain/IP
            raise NeedsInputError(
                "What domain or IP should this app use for Nginx?\n"
                "Examples:\n"
                "  - Domain: pm.meetri.in (will use HTTPS with SSL)\n"
                "  - IP: 192.168.1.1 (will use HTTP only)\n"
                "  - Skip: just press Enter to use server default\n"
                "Enter domain/IP:",
                {"step": "need_domain", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port,
                    "process_manager": ctx.process_manager,
                    "branch": ctx.branch,
                    "app_type": ctx.app_type,
                }, "prefill": pf, "agent": "deployment"}
            )

        self._save_context(ctx)
        logger.info(f"[CONTEXT] app={ctx.app_name} stack={ctx.stack} port={ctx.port} branch={ctx.branch}")
        return ctx

    def _save_context(self, ctx: DeploymentContext) -> None:
        """Saves deployment context to deployment_context.json - reference only, no env_vars duplication."""
        data = {
            "github_url":      ctx.github_url,
            "stack":           ctx.stack,
            "port":            ctx.port,
            "domain":          ctx.domain,
            "app_name":        ctx.app_name,
            "app_path":        ctx.app_path,
            "app_type":        ctx.app_type,
            "env_file_path":   f"{ctx.app_path}/.env",
            "process_manager": ctx.process_manager,
            "branch":          ctx.branch,
        }
        with open(_DEPLOYMENT_CONTEXT_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("[CONTEXT] Saved to deployment_context.json (env_vars stored in server .env only)")

    def _request_ssl_cert(self, domain: str) -> str:
        """Request SSL certificate from Let's Encrypt using certbot (nginx plugin)."""
        import os
        
        # Get default email from environment or use a placeholder
        email = os.getenv("SSL_EMAIL", "admin@server.local")
        
        logger.info(f"[SSL] Requesting certificate for {domain} using email {email}")
        
        # Install certbot and nginx plugin if not present
        self._run(f"which certbot || sudo apt-get update && sudo apt-get install -y certbot python3-certbot-nginx")
        
        # Request certificate using nginx plugin (works with nginx running)
        # This method automatically updates nginx config with SSL settings
        cert_cmd = (
            f"sudo certbot --nginx -d {domain} --non-interactive "
            f"--agree-tos --email {email} --redirect 2>&1"
        )
        
        code, out, err = self.executor.execute(cert_cmd)
        result = out or err
        
        if code == 0 or "Successfully received certificate" in result or "not yet due for renewal" in result or "Certificate not yet due for renewal" in result:
            logger.info(f"[SSL] Certificate obtained and nginx configured for {domain}")
            return f"✅ SSL certificate configured for {domain} with HTTPS redirect"
        else:
            # Non-critical: SSL failure during deployment should not block
            logger.warning(f"[SSL] Certificate request failed (non-critical): {result}")
            return f"⚠️ SSL certificate request failed — configure later with ops agent"


    def _read_env_from_server(self, env_file_path: str) -> dict:
        """Reads environment variables from the server .env file."""
        try:
            _, content, err = self.executor.execute(f"cat {env_file_path}")
            if err or not content:
                logger.warning(f"Could not read {env_file_path} from server")
                return {}
            
            env_vars = {}
            for line in content.strip().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env_vars[key.strip()] = val.strip()
            return env_vars
        except Exception as e:
            logger.error(f"Error reading env from server: {e}")
            return {}

    # ── Multiple Files Detection (Monorepo Support) ───────────────────────────

    def _get_current_git_branch(self, app_path: str) -> str:
        """
        Get the current git branch deployed at app_path.
        Returns the branch name or 'unknown' if not a git repo.
        """
        try:
            cmd = f"git -C {app_path} rev-parse --abbrev-ref HEAD 2>/dev/null"
            code, out, _ = self.executor.execute(cmd)
            if code == 0 and out.strip():
                return out.strip()
        except Exception as e:
            logger.warning(f"[REDEPLOY] Could not determine current branch: {e}")
        return "unknown"

    def _is_redeploy_query(self, query: str) -> bool:
        """
        Check if this is a redeploy request.
        Matches: "redeploy", "re-deploy", "update deploy", etc.
        """
        q_lower = query.lower()
        redeploy_keywords = ("redeploy", "re-deploy", "re deploy", "update deployment", "upgrade deployment", "pull latest", "update app")
        return any(keyword in q_lower for keyword in redeploy_keywords)

    def _detect_deployable_folders(self, root_dir: str) -> dict:
        """
        Scan ALL immediate subfolders for deployment files.
        Does NOT rely on folder names - detects by actual files.
        
        Returns:
            {
                "app-frontend": {      # Actual folder name (could be anything)
                    "path": "app-frontend/",
                    "stack": "nodejs",
                    "file": "package.json",
                    "default_port": 3000,
                },
                "api-service": {       # Actual folder name (could be anything)
                    "path": "api-service/",
                    "stack": "python",
                    "file": "requirements.txt",
                    "default_port": 8000,
                },
            }
        """
        found = {}
        
        try:
            # List ALL immediate subdirectories
            cmd = f"find {root_dir} -maxdepth 1 -type d ! -name '.' ! -name '.git' ! -name '.*' 2>/dev/null | sort"
            code, out, _ = self.executor.execute(cmd)
            
            if code != 0 or not out.strip():
                logger.info(f"[DETECT] No subdirectories found in {root_dir}")
                return found
            
            folders = [f for f in out.strip().split("\n") if f.strip()]
            logger.info(f"[DETECT] Found {len(folders)} subdirectories to scan")
            
            # For EACH folder, try to identify what it is by FILES it contains
            for folder_path in folders:
                folder_name = folder_path.rstrip("/").split("/")[-1]
                
                # Skip hidden folders and common non-app directories
                if folder_name.startswith(".") or folder_name in ("node_modules", "__pycache__", ".git", ".github"):
                    continue
                
                # Try to identify deployment type
                deployment_info = self._identify_folder_by_files(folder_path)
                
                if deployment_info:
                    found[folder_name] = deployment_info
                    logger.info(f"[DETECT] {folder_name}: {deployment_info['stack']} ({deployment_info['file']})")
        
        except Exception as e:
            logger.error(f"[DETECT] Error scanning directories: {e}")
        
        return found

    def _identify_folder_by_files(self, folder_path: str) -> dict:
        """
        Identify what type of app is in this folder by checking for files.
        Does NOT use folder name - only looks at files.
        
        Returns: dict with stack, file, default_port OR None if no files found
        """
        
        # Check for each stack type (in priority order)
        # Docker takes priority over language-specific files
        checks = [
            {
                "stack": "docker",
                "files": ["Dockerfile"],
                "default_port": 8080,
            },
            {
                "stack": "nodejs",
                "files": ["package.json"],
                "default_port": 3000,
            },
            {
                "stack": "python",
                "files": ["requirements.txt", "setup.py", "pyproject.toml", "pipfile"],
                "default_port": 8000,
            },
            {
                "stack": "ruby",
                "files": ["Gemfile"],
                "default_port": 3000,
            },
            {
                "stack": "go",
                "files": ["go.mod"],
                "default_port": 8080,
            },
            {
                "stack": "rust",
                "files": ["Cargo.toml"],
                "default_port": 8080,
            },
            {
                "stack": "php",
                "files": ["composer.json", "index.php"],
                "default_port": 8000,
            },
            {
                "stack": "java",
                "files": ["pom.xml", "build.gradle"],
                "default_port": 8080,
            },
            {
                "stack": "dotnet",
                "files": ["*.csproj"],
                "default_port": 5000,
            },
        ]
        
        for check in checks:
            for file_to_check in check["files"]:
                # Handle glob patterns like *.csproj
                if "*" in file_to_check:
                    # Use find with glob
                    cmd = f"find {folder_path} -maxdepth 1 -name '{file_to_check}' -type f 2>/dev/null | head -1"
                    code, out, _ = self.executor.execute(cmd)
                    if code == 0 and out.strip():
                        return {
                            "stack": check["stack"],
                            "file": file_to_check,
                            "default_port": check["default_port"],
                        }
                else:
                    # Simple file check
                    cmd = f"test -f {folder_path}/{file_to_check} && echo found 2>/dev/null"
                    code, out, _ = self.executor.execute(cmd)
                    
                    if code == 0 and "found" in out:
                        return {
                            "stack": check["stack"],
                            "file": file_to_check,
                            "default_port": check["default_port"],
                        }
        
        return None

    def _detect_same_folder_multiple_stacks(self, root_dir: str) -> dict:
        """
        Detect if the same folder has multiple deployment files.
        Example: package.json + requirements.txt in same directory
        
        Returns:
            {
                "nodejs": {"file": "package.json", "default_port": 3000},
                "python": {"file": "requirements.txt", "default_port": 8000},
            }
        """
        found = {}
        
        checks = [
            {
                "stack": "docker",
                "files": ["Dockerfile"],
                "default_port": 8080,
            },
            {
                "stack": "nodejs",
                "files": ["package.json"],
                "default_port": 3000,
            },
            {
                "stack": "python",
                "files": ["requirements.txt", "setup.py", "pyproject.toml", "pipfile"],
                "default_port": 8000,
            },
            {
                "stack": "ruby",
                "files": ["Gemfile"],
                "default_port": 3000,
            },
            {
                "stack": "go",
                "files": ["go.mod"],
                "default_port": 8080,
            },
            {
                "stack": "rust",
                "files": ["Cargo.toml"],
                "default_port": 8080,
            },
        ]
        
        for check in checks:
            for file_to_check in check["files"]:
                cmd = f"test -f {root_dir}/{file_to_check} && echo found"
                code, out, _ = self.executor.execute(cmd)
                
                if code == 0 and "found" in out:
                    found[check["stack"]] = {
                        "file": file_to_check,
                        "default_port": check["default_port"],
                    }
                    break  # Found this stack, move to next
        
        return found

    def _parse_deployment_choice(self, choice: str, available: dict) -> list:
        """
        Parse user choice for multiple deployment targets.
        
        Accepts:
        - "1" or "2" (numeric indices, 1-indexed)
        - "both" or "all" (deploy all)
        - folder name or partial match
        - comma-separated choices "1,2"
        
        Returns: list of folder names to deploy
        """
        choice = choice.strip().lower()
        available_list = list(available.keys())
        
        # "both" or "all" — deploy all available
        if choice in ("both", "all", "a", ""):
            return available_list
        
        # Comma-separated choices "1,2" or "frontend,backend"
        if "," in choice:
            results = []
            for part in choice.split(","):
                part = part.strip().lower()
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(available_list):
                        results.append(available_list[idx])
                except ValueError:
                    # Try name match
                    for folder in available_list:
                        if part in folder.lower() or folder.lower() in part:
                            if folder not in results:
                                results.append(folder)
                            break
            return results if results else available_list
        
        # Single numeric choice "1" or "2"
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available_list):
                return [available_list[idx]]
        except ValueError:
            pass
        
        # Try name match "nodejs" or "frontend" or "app-frontend"
        for folder in available_list:
            if choice in folder.lower() or folder.lower() in choice:
                return [folder]
        
        # Default to all if no match
        logger.warning(f"[DEPLOY] Unknown choice '{choice}' — defaulting to all ({available_list})")
        return available_list

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _run(self, cmd: str) -> str:
        logger.info(f"  [RUN] {cmd[:160]}")
        result = self.linux.run_custom_command(cmd)
        logger.info(f"  [OK]  {str(result)[:120]}")
        return result

    def _inspect(self, cmd: str) -> str:
        result = self.linux.inspect_path(cmd)
        logger.info(f"  [INSPECT] {cmd} → {str(result)[:120]}")
        return result

    def _file_exists(self, path: str) -> bool:
        code, _, _ = self.executor.execute(f"test -f {path}")
        return code == 0

    def _dir_exists(self, path: str) -> bool:
        code, _, _ = self.executor.execute(f"test -d {path}")
        return code == 0

    # ── Phase 2 steps (pure Python, no LLM) ───────────────────────────────────

    def _step_clone(self, ctx: DeploymentContext) -> None:
        logger.info("[STEP 1/5] Clone")

        # Resolve $HOME to the actual home directory on the server
        if "$HOME" in ctx.app_path or ctx.app_path.startswith("~"):
            _, home_out, _ = self.executor.execute("echo $HOME")
            home = home_out.strip()
            if home:
                ctx.app_path = ctx.app_path.replace("$HOME", home).replace("~", home)
                logger.info(f"  [CLONE] Resolved app_path to {ctx.app_path}")

        # Build authenticated clone URL
        from app.core.config import settings
        token = getattr(self, '_github_token', None) or settings.GITHUB_TOKEN
        if not token:
            result = self._inspect("cat ~/.github_token 2>/dev/null")
            if result.strip() and "COMMAND DID NOT SUCCEED" not in result:
                token = result.strip()

        repo_path = ctx.github_url.replace("https://", "")
        clone_url = f"https://{token}@{repo_path}" if token else ctx.github_url
        if not token:
            logger.warning("No GitHub token — attempting unauthenticated clone.")

        # Check if a valid git repo already exists at this exact path
        git_code, _, _ = self.executor.execute(
            f"git --git-dir={ctx.app_path}/.git rev-parse --git-dir 2>/dev/null"
        )
        if git_code == 0:
            logger.info(f"[CLONE] Repo exists at {ctx.app_path} — pulling latest")
            self._run(f"git -C {ctx.app_path} pull")
        else:
            from app.services.conversation_service import NeedsInputError
            
            # Check if directory exists but is NOT a git repo
            dir_exists_code, dir_out, _ = self.executor.execute(f"test -d {ctx.app_path} && echo 'exists' || echo 'missing'")
            dir_exists = "exists" in dir_out
            
            if dir_exists:
                logger.info(f"[CLONE] Directory exists at {ctx.app_path} but not a git repo — cloning into it")
            else:
                logger.info(f"[CLONE] Fresh clone into {ctx.app_path} — directory doesn't exist yet")
                
                # Directory doesn't exist, ask for permission to create it
                delete_confirm = getattr(self, '_delete_confirm', None) or (self._prefill or {}).get('delete_confirm')
                if delete_confirm is None:
                    raise NeedsInputError(
                        f"⚠️ **Fresh deployment detected**\n\n"
                        f"I need to create and populate the directory:\n"
                        f"`{ctx.app_path}`\n\n"
                        f"This will:\n"
                        f"- Create the directory\n"
                        f"- Clone the repository\n"
                        f"- Set up the application\n\n"
                        f"Type **yes** to proceed, or **no** to cancel.",
                        {
                            "step": "confirm_fresh_clone",
                            "app_path": ctx.app_path,
                            "app_name": ctx.app_name,
                            "ctx_partial": {
                                "github_url": ctx.github_url,
                                "stack": ctx.stack,
                                "port": ctx.port,
                                "domain": ctx.domain,
                                "process_manager": ctx.process_manager,
                                "branch": ctx.branch,
                                "app_name": ctx.app_name,
                                "app_path": ctx.app_path,
                                "env_vars": ctx.env_vars,
                                "app_type": ctx.app_type,
                            },
                            "agent": "deployment",
                        }
                    )
                
                # User confirmed
                if delete_confirm not in ("yes", "y"):
                    raise RuntimeError(f"Fresh clone cancelled by user. Directory {ctx.app_path} was NOT created.")
            
            # Clone without sudo — user typically owns their app directory
            # (e.g., /home/meetri/api is owned by meetri user)
            # Use GIT_ASKPASS=echo to suppress TTY prompt and pass token inline in URL.
            # This works in headless environments where there's no TTY available.
            try:
                self._run(
                    f"bash -c 'GIT_ASKPASS=echo git clone "
                    f"--branch {ctx.branch} --single-branch "
                    f"{clone_url} {ctx.app_path}'"
                )
            except RuntimeError as e:
                error_msg = str(e)
                if "Authentication failed" in error_msg or "Invalid username or token" in error_msg:
                    raise RuntimeError(
                        f"Git clone failed due to authentication error.\n"
                        f"Details: {error_msg}\n\n"
                        f"Possible causes:\n"
                        f"1. GitHub token is invalid or expired\n"
                        f"2. Token does not have access to this repository\n"
                        f"3. Repository is private and token is missing\n\n"
                        f"Solution: Provide a valid GitHub PAT at https://github.com/settings/tokens/new?scopes=repo"
                    )
                if "Permission denied" in error_msg or "cannot create" in error_msg.lower():
                    raise RuntimeError(
                        f"Permission denied trying to clone to {ctx.app_path}.\n"
                        f"Details: {error_msg}\n\n"
                        f"Make sure you own the parent directory or have write permissions."
                    )
                raise
        files = self._inspect(f"ls {ctx.app_path}")
        if not files.strip() or "COMMAND DID NOT SUCCEED" in files:
            raise RuntimeError(f"Clone succeeded but {ctx.app_path} is empty.")

    def _step_write_env(self, ctx: DeploymentContext) -> None:
        logger.info("[STEP 2/5] Write .env")
        env_content = "\n".join(f"{k}={v}" for k, v in ctx.env_vars.items())
        encoded = base64.b64encode(env_content.encode()).decode()
        self._run(
            f"echo '{encoded}' | base64 --decode > {ctx.app_path}/.env"
        )
        logger.info(f"  .env written ({len(ctx.env_vars)} vars)")

    def _detect_actual_port(self, app_name: str, expected_port: str) -> str:
        """After PM2 start, read actual port from logs and ss."""
        import time as _t
        _t.sleep(3)

        _, logs, _ = self.executor.execute(
            f"pm2 logs {app_name} --lines 50 --nostream --no-color 2>/dev/null"
        )
        match = re.search(r'(?:port|PORT|listening)[^\d]*(\d{3,5})', logs, re.IGNORECASE)
        if match:
            detected = match.group(1)
            if detected != expected_port:
                logger.warning(f"  [PORT] App bound to {detected}, not {expected_port}. Using {detected}.")
            return detected

        _, ss_out, _ = self.executor.execute("ss -tlnp | grep node")
        match = re.search(r':(\d{3,5})\s', ss_out)
        if match:
            detected = match.group(1)
            if detected != expected_port:
                logger.warning(f"  [PORT] ss shows node on {detected}, not {expected_port}. Using {detected}.")
            return detected

        logger.info(f"  [PORT] Could not auto-detect. Using specified: {expected_port}")
        return expected_port

    def _step_install(self, ctx: DeploymentContext) -> None:
        logger.info(f"[STEP 3/5] Install ({ctx.stack} via {ctx.process_manager})")
        stack = ctx.stack
        app_path = ctx.app_path
        app_name = ctx.app_name
        port     = ctx.port
        pm       = ctx.process_manager

        # ── Auto-detect nested app directory ──────────────────────────────
        # Some repos have structure: repo/ → app_dir/ → package.json
        # Check if app_path points to parent, and app files are in subdirectory
        has_app_here = (
            self._file_exists(f"{app_path}/requirements.txt") or 
            self._file_exists(f"{app_path}/package.json") or
            self._file_exists(f"{app_path}/Dockerfile")
        )
        
        if not has_app_here:
            # Try common subdirectories first
            for subdir_candidate in ("server_setup_agent", "src", "app", "backend", "frontend", "client", "web"):
                candidate_path = f"{app_path}/{subdir_candidate}"
                if self._file_exists(f"{candidate_path}/requirements.txt") or self._file_exists(f"{candidate_path}/package.json"):
                    logger.info(f"  [INSTALL] Auto-detected nested app directory: {candidate_path}")
                    app_path = candidate_path
                    ctx.app_path = candidate_path
                    break
            else:
                # If common subdirs didn't work, find first subdirectory with app files
                try:
                    code, ls_out, _ = self.executor.execute(f"ls -1d {app_path}/*/ 2>/dev/null | head -20")
                    if ls_out.strip():
                        for line in ls_out.strip().split("\n"):
                            candidate_path = line.rstrip("/").strip()
                            if candidate_path:
                                has_app = (
                                    self._file_exists(f"{candidate_path}/package.json") or
                                    self._file_exists(f"{candidate_path}/requirements.txt") or
                                    self._file_exists(f"{candidate_path}/Dockerfile")
                                )
                                if has_app:
                                    logger.info(f"  [INSTALL] Auto-detected nested app directory (via scan): {candidate_path}")
                                    app_path = candidate_path
                                    ctx.app_path = candidate_path
                                    break
                except Exception as e:
                    logger.warning(f"  [INSTALL] Could not scan subdirectories: {e}")
            
            # If still no app found, show what was in the directory
            if not (self._file_exists(f"{app_path}/package.json") or self._file_exists(f"{app_path}/requirements.txt")):
                logger.warning(f"  [INSTALL] No app files found at {app_path}. Contents:")
                try:
                    self.executor.execute(f"ls -la {app_path} | head -30")
                except Exception:
                    pass

        # ── Static (react / vite / angular) ───────────────────────────────
        if stack in ("react", "vite", "angular"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            self._run(f"npm run build --prefix {app_path}")

            if pm == "pm2":
                # Serve the built dist/ folder using npx serve (no global install needed)
                # This avoids the sudo terminal issue — npx handles everything
                self.pm2.install()
                # Find the actual build output folder
                dist_path = app_path + "/dist"
                for candidate in ("dist", "build", "out"):
                    if self._dir_exists(f"{app_path}/{candidate}"):
                        dist_path = f"{app_path}/{candidate}"
                        break
                # pm2 start npx -- serve -s <dist> -l <port>
                self.executor.execute(f"pm2 delete {app_name} 2>/dev/null || true")
                cmd = (
                    f"cd {app_path} && "
                    f"pm2 start npx --name {app_name} -- serve -s {dist_path} -l {port}"
                )
                code, out, err = self.executor.execute(cmd)
                if code != 0:
                    raise RuntimeError(f"PM2 serve failed:\n{err}\n{out}")
                self.pm2.save()
            elif pm == "docker":
                self._deploy_docker(ctx)
            # systemd or no pm — nginx will serve the static files directly
            return

        # ── Node / Next.js / NestJS ────────────────────────────────────────
        if stack in ("nextjs", "nodejs", "nestjs"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            if stack in ("nextjs", "nestjs"):
                self._run(f"npm run build --prefix {app_path}")
            
            # Determine the startup script
            # For NestJS and Next.js, use 'npm run start' (respects package.json scripts)
            # For plain Node.js, use 'npm start' or 'index.js'
            if stack in ("nextjs", "nestjs"):
                entry = "npm"  # Will use 'npm run start' in PM2
            else:
                entry = "index.js"

            if pm == "pm2":
                self.pm2.install()
                self.pm2.start(app_name=app_name, script=entry, working_directory=app_path, port=port)
                self.pm2.save()
                ctx.port = self._detect_actual_port(app_name, ctx.port)

            elif pm == "systemd":
                if stack == "nextjs":
                    exec_start = f"/usr/bin/npm --prefix {app_path} run start"
                elif stack == "nestjs":
                    exec_start = f"/usr/bin/npm --prefix {app_path} run start"
                else:
                    exec_start = f"/usr/bin/node {app_path}/index.js"
                self.systemd.create_service_file(
                    service_name=app_name,
                    exec_start=exec_start,
                    working_directory=app_path,
                )
                self.systemd.start_service(app_name)
                self.systemd.enable_service(app_name)

            elif pm == "docker":
                self._deploy_docker(ctx)

        # ── Python (fastapi / flask / django) ──────────────────────────────
        elif stack in ("fastapi", "flask", "django"):
            if not self._file_exists(f"{app_path}/requirements.txt"):
                raise RuntimeError(f"No requirements.txt found at {app_path}.")

            if pm == "docker":
                self._deploy_docker(ctx)
                return

            # Install deps regardless of pm
            self.pkg.install("python3-venv")
            self._run(f"python3 -m venv {app_path}/venv")
            self._run(f"{app_path}/venv/bin/pip install -r {app_path}/requirements.txt")

            if stack == "fastapi":
                exec_cmd = f"{app_path}/venv/bin/uvicorn main:app --host 0.0.0.0 --port {port}"
            elif stack == "flask":
                exec_cmd = f"{app_path}/venv/bin/python app.py"
            else:
                exec_cmd = f"{app_path}/venv/bin/python manage.py runserver 0.0.0.0:{port}"

            if pm == "pm2":
                self.pm2.install()
                # PM2 starts python via interpreter
                self.pm2.start(
                    app_name=app_name,
                    script=exec_cmd.split()[0],   # the binary
                    working_directory=app_path,
                )
                self.pm2.save()

            else:  # systemd (default for Python)
                self.systemd.create_service_file(
                    service_name=app_name,
                    exec_start=exec_cmd,
                    working_directory=app_path,
                )
                self.systemd.start_service(app_name)
                self.systemd.enable_service(app_name)

        else:
            raise RuntimeError(f"Unsupported stack: {stack}")

    def _deploy_docker(self, ctx: DeploymentContext) -> None:
        """Build and run app using Docker."""
        logger.info(f"  [DOCKER] Building {ctx.app_name}")

        app_path = ctx.app_path

        # ── Auto-detect nested app directory ──────────────────────────────
        # Some repos have structure: repo/ → app_dir/ → Dockerfile
        if not self._file_exists(f"{app_path}/Dockerfile"):
            # Try common subdirectories first
            for subdir_candidate in ("server_setup_agent", "src", "app", "backend", "frontend", "client", "web"):
                candidate_path = f"{app_path}/{subdir_candidate}"
                if self._file_exists(f"{candidate_path}/Dockerfile"):
                    logger.info(f"  [DOCKER] Auto-detected nested app directory: {candidate_path}")
                    app_path = candidate_path
                    ctx.app_path = candidate_path
                    break
            else:
                # If common subdirs didn't work, find first subdirectory with Dockerfile
                try:
                    code, ls_out, _ = self.executor.execute(f"ls -1d {app_path}/*/ 2>/dev/null | head -20")
                    if ls_out.strip():
                        for line in ls_out.strip().split("\n"):
                            candidate_path = line.rstrip("/").strip()
                            if candidate_path and self._file_exists(f"{candidate_path}/Dockerfile"):
                                logger.info(f"  [DOCKER] Auto-detected nested app directory (via scan): {candidate_path}")
                                app_path = candidate_path
                                ctx.app_path = candidate_path
                                break
                except Exception as e:
                    logger.warning(f"  [DOCKER] Could not scan subdirectories: {e}")

        # Handle case-insensitive Dockerfile names (repo has 'DockerFile' not 'Dockerfile')
        # Normalize to lowercase so docker build works consistently
        self.executor.execute(
            f"test -f {app_path}/DockerFile "
            f"&& mv {app_path}/DockerFile {app_path}/Dockerfile 2>/dev/null "
            f"|| true"
        )

        # Auto-generate Dockerfile if still missing but repo has requirements.txt/package.json
        if not self._file_exists(f"{app_path}/Dockerfile"):
            has_requirements = self._file_exists(f"{app_path}/requirements.txt")
            has_package_json = self._file_exists(f"{app_path}/package.json")
            if has_requirements or has_package_json:
                logger.info(f"  [DOCKER] No Dockerfile found — auto-generating for {ctx.stack}")
                self._generate_dockerfile(ctx)
            else:
                raise RuntimeError(
                    f"No Dockerfile found at {app_path} and no requirements.txt/package.json "
                    f"to auto-generate one. Please add a Dockerfile to the repo."
                )
        # Install Docker if needed
        code, _, _ = self.executor.execute("which docker")
        if code != 0:
            self._run("sudo apt-get update -y && sudo apt-get install -y docker.io")
            self._run("sudo systemctl start docker && sudo systemctl enable docker")
            self._run(f"sudo usermod -aG docker $(whoami)")

        # Stop existing container
        self.executor.execute(f"sudo docker stop {ctx.app_name} 2>/dev/null || true")
        self.executor.execute(f"sudo docker rm {ctx.app_name} 2>/dev/null || true")

        # Build image
        self._run(f"sudo docker build -t {ctx.app_name} {ctx.app_path}")

        # Run container
        # Properly quote env values to handle special characters and spaces
        env_flags = " ".join(f'-e "{k}={v}"' for k, v in ctx.env_vars.items())
        self._run(
            f"sudo docker run -d --name {ctx.app_name} "
            f"--restart unless-stopped "
            f"-p {ctx.port}:{ctx.port} "
            f"{env_flags} "
            f"{ctx.app_name}"
        )
        logger.info(f"  [DOCKER] Container {ctx.app_name} running on port {ctx.port}")

    def _check_port_conflict(self, port: str, app_name: str = "") -> str:
        """
        Checks if the given port is already in use on the server.
        - If occupied by the same app (re-deployment): stops it automatically and continues.
        - If occupied by a different process: raises NeedsInputError to ask the user.
        Returns the confirmed port.
        """
        from app.services.conversation_service import NeedsInputError

        _, ss_out, _ = self.executor.execute(f"ss -tlnp | grep :{port} ")
        if not ss_out.strip():
            logger.info(f"  [PORT CHECK] Port {port} is free.")
            return port

        # Port is in use — find out what's using it
        _, pid_info, _ = self.executor.execute(
            f"ss -tlnp | grep :{port} | grep -oP 'pid=\\K[0-9]+'"
        )
        pid = pid_info.strip().split("\n")[0]
        process_name = ""
        if pid:
            _, pname, _ = self.executor.execute(f"ps -p {pid} -o comm= 2>/dev/null")
            process_name = pname.strip()

        logger.warning(f"  [PORT CONFLICT] Port {port} is already in use by '{process_name}' (pid {pid})")

        # ── Auto-resolve: same app re-deployment ──────────────────────────
        # If it's a next-server, node, or pm2 process AND we have an app_name,
        # it's almost certainly the previous deployment of this same app — just kill it.
        is_node_process = any(
            kw in process_name.lower()
            for kw in ("next-server", "node", "npm", "pm2")
        )
        if is_node_process and app_name:
            logger.info(f"  [PORT] Same-app conflict detected — stopping pm2 process '{app_name}' and continuing.")
            self.executor.execute(f"pm2 delete {app_name} 2>/dev/null || true")
            self.executor.execute(f"kill -9 {pid} 2>/dev/null || true")
            return port

        # ── Unknown process — ask user via chat ───────────────────────────
        pf = getattr(self, '_prefill', {}) or {}
        port_conflict_answer = pf.get("port_conflict_answer")
        if port_conflict_answer is not None:
            if str(port_conflict_answer).isdigit():
                logger.info(f"  [PORT] User switched port from {port} to {port_conflict_answer}")
                return str(port_conflict_answer)
            logger.info(f"  [PORT] User chose to continue with port {port} despite conflict.")
            return port

        raise NeedsInputError(
            f"⚠ Port `{port}` is already in use by `{process_name or 'unknown'}` (pid={pid or '?'}).\n"
            f"Options:\n"
            f"- Enter a **different port number** to use instead\n"
            f"- Type `continue` to proceed anyway",
            {
                "step": "need_port_conflict",
                "port": port,
                "process_name": process_name,
                "pid": pid,
                "ctx_partial": (pf.get("ctx_partial") or {}),
                "prefill": pf,
                "agent": "deployment",
            }
        )

    def _step_nginx(self, ctx: DeploymentContext) -> None:
        logger.info("[STEP 4/5] Nginx")
        from app.services.conversation_service import NeedsInputError

        # ── Ask user if they want nginx configured ─────────────────────────
        nginx_choice = getattr(self, '_nginx_choice', None) or (self._prefill or {}).get('nginx_choice')
        if nginx_choice is None:
            domain_display = ctx.domain if ctx.domain and ctx.domain != "_" else "<server IP>"
            raise NeedsInputError(
                f"Do you want me to configure Nginx for **{ctx.app_name}**?\n"
                f"- Type `yes` to set up Nginx (will proxy `http://{domain_display}` → `localhost:{ctx.port}`)\n"
                f"- Type `no` to skip (app will only be accessible on port `{ctx.port}` directly)\n"
                f"- Or type a custom domain/IP if you want to use a different one than `{domain_display}`",
                {
                    "step": "need_nginx",
                    "ctx_partial": {
                        "github_url":        ctx.github_url,
                        "stack":             ctx.stack,
                        "port":              ctx.port,
                        "domain":            ctx.domain,
                        "process_manager":   ctx.process_manager,
                        "branch":            ctx.branch,
                        "app_name":          ctx.app_name,
                        "app_path":          ctx.app_path,
                        "env_vars":          ctx.env_vars,
                        "app_type":          ctx.app_type,
                        "deploy_steps_done": True,
                    },
                    "prefill": self._prefill or {},
                    "agent": "deployment",
                }
            )

        # User said no — skip nginx entirely
        if nginx_choice == "no":
            logger.info("[STEP 4/5] Nginx skipped by user")
            return

        # User provided a custom domain — override ctx.domain
        if nginx_choice not in ("yes", "y", "skip"):
            ctx.domain = nginx_choice
            self._save_context(ctx)

        # ── Check if sudo_password is available for nginx configuration ────
        # Nginx requires sudo for installing, configuring, and reloading
        if self.executor_type == "ssh":
            executor_config = getattr(self, 'executor_config', {})
            sudo_password = executor_config.get('sudo_password')
            
            # Check if sudo_password was provided in prefill (from user answer)
            if not sudo_password and self._prefill:
                sudo_password = self._prefill.get('sudo_password')
            
            if not sudo_password:
                raise NeedsInputError(
                    f"⚠️ **Sudo Password Required**\n\n"
                    f"Nginx configuration requires sudo privileges to:\n"
                    f"- Install nginx (if not already installed)\n"
                    f"- Write config files to /etc/nginx/\n"
                    f"- Reload nginx service\n\n"
                    f"Please provide the sudo password for user `{executor_config.get('username', 'root')}`:",
                    {
                        "step": "need_sudo_password",
                        "ctx_partial": {
                            "github_url":        ctx.github_url,
                            "stack":             ctx.stack,
                            "port":              ctx.port,
                            "domain":            ctx.domain,
                            "process_manager":   ctx.process_manager,
                            "branch":            ctx.branch,
                            "app_name":          ctx.app_name,
                            "app_path":          ctx.app_path,
                            "env_vars":          ctx.env_vars,
                            "app_type":          ctx.app_type,
                            "deploy_steps_done": True,
                            "nginx_choice":      nginx_choice,
                        },
                        "prefill": self._prefill or {},
                        "agent": "deployment",
                    }
                )

        self.nginx.install()

        # ── Ensure SSL certificate exists for domain-based deployments ──────
        if ctx.domain and ctx.domain != "_" and not self.nginx._is_ip(ctx.domain):
            ssl_status = self.nginx.ensure_ssl_cert(ctx.domain)
            logger.info(f"SSL cert status: {ssl_status}")

        # ── Always use reverse proxy (consistent for all stacks) ────────────
        # Frontend (React/Vite/Angular) runs on port via `serve` or dev server
        # Backend (Flask/FastAPI/Express) runs on port directly
        # Nginx proxies to the port for all stacks
        self.nginx.generate_and_save_config(
            framework=ctx.stack,
            domain=ctx.domain,
            app_name=ctx.app_name,
            port=int(ctx.port),
        )

        self.nginx.test_config()
        self.nginx.enable_site(ctx.app_name, ctx.domain)
        self.nginx.reload_nginx()

        # ── Configure SSL certificate automatically if domain is provided ────
        if ctx.domain and ctx.domain != "_" and not self.nginx._is_ip(ctx.domain):
            logger.info(f"[STEP 4/5] Configuring SSL certificate for {ctx.domain}")
            try:
                # Request SSL certificate using Let's Encrypt
                ssl_result = self._request_ssl_cert(ctx.domain)
                logger.info(f"[SSL] Certificate result: {ssl_result}")
            except Exception as e:
                logger.warning(f"[SSL] Could not configure certificate during deployment: {e}")
                logger.info(f"[SSL] You can configure SSL later using the ops agent")


    # ── Public entry point ─────────────────────────────────────────────────────

    def execute_task(self, query: str) -> str:
        from app.services.conversation_service import NeedsInputError
        
        # ── Update executor with sudo_password if provided in prefill ────
        pf = getattr(self, '_prefill', None) or {}
        if pf.get('sudo_password') and hasattr(self.executor, 'sudo_password'):
            self.executor.sudo_password = pf['sudo_password']
            logger.info(f"[DEPLOY] ✓ Sudo password loaded from frontend/prefill")
        elif hasattr(self.executor, 'sudo_password') and self.executor.sudo_password:
            logger.info(f"[DEPLOY] ✓ Sudo password loaded from .env")
        elif self.executor_type == "ssh":
            # Check if user is root (root may not need sudo password)
            executor_config = getattr(self, 'executor_config', {})
            username = executor_config.get('username', 'root')
            
            # If not root, sudo password is required for package installation
            if username != 'root':
                logger.warning(f"[DEPLOY] ✗ No sudo password available - deployment will fail")
                raise NeedsInputError(
                    f"⚠️ **Sudo Password Required**\n\n"
                    f"Deployment requires sudo privileges to:\n"
                    f"- Install system packages (nodejs, python3-venv, etc.)\n"
                    f"- Configure nginx web server\n"
                    f"- Manage system services\n\n"
                    f"Please provide the sudo password for user `{username}`:",
                    {
                        "step": "need_sudo_password",
                        "ctx_partial": {},  # Will be filled when context is gathered
                        "prefill": pf,  # Use pf instead of self._prefill (already safe with getattr)
                        "agent": "deployment",
                    }
                )
            else:
                logger.info(f"[DEPLOY] Connected as root - sudo password not required")
        
        # ── Fast-path: resuming after need_nginx question ─────────────────
        # Only skip clone+install if this is a genuine mid-deployment resume:
        # - nginx_choice must be set (user just answered the nginx question)
        # - app_path must be a fully resolved absolute path (no $HOME placeholder)
        # - deploy_steps_done flag must be set (proves clone+install completed this session)
        nginx_only = (
            pf.get('nginx_choice') is not None
            and pf.get('app_path')
            and not pf.get('app_path', '').startswith('$HOME')
            and pf.get('deploy_steps_done') is True
        )
        if nginx_only:
            logger.info("[PHASE 2] Resuming nginx-only step after user answered nginx question")
            ctx = DeploymentContext(
                github_url      = pf.get("github_url", "https://github.com/x/x.git"),
                stack           = pf.get("stack", ""),
                port            = str(pf.get("port", "")),
                domain          = pf.get("domain", "_"),
                env_vars        = pf.get("env_vars", {}),
                process_manager = pf.get("process_manager", ""),
                branch          = pf.get("branch", "main"),
                app_type        = pf.get("app_type", ""),
            )
            # Restore derived fields directly — don't re-derive from github_url
            ctx.app_name = pf.get("app_name", ctx.app_name)
            ctx.app_path = pf.get("app_path", ctx.app_path)
            self._nginx_choice = pf["nginx_choice"]
            try:
                self._step_nginx(ctx)
            except Exception as e:
                from app.services.conversation_service import NeedsInputError
                if isinstance(e, NeedsInputError):
                    raise
                return f"DEPLOYMENT FAILED at nginx.\n{e}"
            domain_display = ctx.domain if ctx.domain and ctx.domain != "_" else "<server IP>"
            is_ip = NginxTool._is_ip(ctx.domain) or ctx.domain in ("_", "", None)
            protocol = "http" if is_ip else "https"
            return (
                f"DEPLOYMENT COMPLETE\n"
                f"App:    {ctx.app_name}\n"
                f"URL:    {protocol}://{domain_display}\n"
                f"Port:   {ctx.port}\n"
                f"Stack:  {ctx.stack}\n"
                f"✓ nginx configured"
            )

        # ── Phase 1: collect all required info via one LLM call ───────────
        logger.info("[PHASE 1] Gathering deployment context via LLM...")
        ctx = self._gather_context(query)

        logger.info(
            f"[PHASE 2] Starting deployment: {ctx.app_name} | "
            f"stack={ctx.stack} | port={ctx.port} | domain={ctx.domain}"
        )

        # Check port conflict before touching the server
        ctx.port = self._check_port_conflict(ctx.port, app_name=ctx.app_name)

        # If .env has a PORT key that conflicts with the confirmed port, override it
        if ctx.env_vars:
            port_keys = [k for k in ctx.env_vars if re.search(r'\bPORT\b', k, re.IGNORECASE)]
            for key in port_keys:
                if ctx.env_vars[key] != ctx.port:
                    logger.warning(
                        f"  [ENV OVERRIDE] {key}={ctx.env_vars[key]} → {ctx.port} "
                        f"(overriding to match confirmed free port)"
                    )
                    print(f"\n  ⚠ .env has {key}={ctx.env_vars[key]} but confirmed port is {ctx.port}.")
                    print(f"  Overriding {key} to {ctx.port} to avoid conflict.")
                    ctx.env_vars[key] = ctx.port

        # Save updated context (port + env_vars may have changed)
        self._save_context(ctx)

        results = []

        # ── Handle REDEPLOY (pull latest from current branch) ────────────────
        if pf.get("redeploy"):
            logger.info(f"[REDEPLOY] Pulling latest changes from {pf.get('current_branch', 'main')}")
            
            # Skip clone step, go straight to pull
            branch = pf.get("current_branch", "main")
            try:
                # Pull latest changes
                self._run(f"git -C {ctx.app_path} fetch origin {branch}")
                self._run(f"git -C {ctx.app_path} reset --hard origin/{branch}")
                results.append(f"✓ pull (branch: {branch})")
            except Exception as e:
                logger.error(f"[REDEPLOY FAILED] Pull failed: {e}")
                self.alerter.critical(
                    title=f"Redeploy FAILED: {ctx.app_name} — git pull error",
                    server=self._server,
                    details=str(e)[:300],
                )
                return f"REDEPLOY FAILED at git pull.\n{e}"
            
            # Skip to install step (don't clone, just pull and reinstall)
            # Mark that we've already handled clone-equivalent
            skip_clone = True
        else:
            skip_clone = False

        # ── Step 1: Clone ──────────────────────────────────────────────────
        if not skip_clone:
            try:
                self._step_clone(ctx)
                results.append("✓ clone")
            except Exception as e:
                from app.services.conversation_service import NeedsInputError
                if isinstance(e, NeedsInputError):
                    # Augment the error with full deployment context for proper resumption
                    if not e.state:
                        e.state = {}
                    if "ctx_partial" not in e.state:
                        e.state["ctx_partial"] = {
                            "github_url": ctx.github_url,
                            "stack": ctx.stack,
                            "port": ctx.port,
                            "domain": ctx.domain,
                            "process_manager": ctx.process_manager,
                            "branch": ctx.branch,
                            "app_type": ctx.app_type,
                            "app_name": ctx.app_name,
                            "app_path": ctx.app_path,
                        }
                    raise  # Re-raise to let API handle the prompt
                logger.error(f"[STEP 1 FAILED] {e}")
                self.alerter.critical(
                    title=f"Deployment FAILED: {ctx.app_name} — clone error",
                    server=self._server,
                    details=str(e)[:300],
                )
                return f"DEPLOYMENT FAILED at clone.\n{e}"

        # ── Step 1.5: Detect monorepo/multiple files ───────────────────────
        # After clone is successful, check if this is a monorepo or has multiple deployment files
        try:
            # Check for multiple deployment files in the SAME FOLDER
            same_folder_stacks = self._detect_same_folder_multiple_stacks(ctx.app_path)
            if len(same_folder_stacks) > 1:
                # Multiple stacks in the same folder — ask which to deploy
                options = "\n".join(
                    f"  {i+1}. {stack.upper()} ({info['file']})"
                    for i, (stack, info) in enumerate(same_folder_stacks.items())
                )
                raise NeedsInputError(
                    f"Multiple deployment files detected in the same folder:\n\n{options}\n\n"
                    f"Which stack should I deploy?\n"
                    f"(Enter number, e.g., '1', '2', or the stack name)",
                    {
                        "step": "choose_same_folder_stack",
                        "detected_stacks": same_folder_stacks,
                        "ctx_partial": {
                            "github_url": ctx.github_url,
                            "stack": ctx.stack,
                            "port": ctx.port,
                            "domain": ctx.domain,
                            "process_manager": ctx.process_manager,
                            "branch": ctx.branch,
                            "app_type": ctx.app_type,
                            "app_name": ctx.app_name,
                            "app_path": ctx.app_path,
                        },
                        "prefill": pf,
                        "agent": "deployment",
                    }
                )
            
            # Check for multiple deployable folders (monorepo)
            detected_folders = self._detect_deployable_folders(ctx.app_path)
            
            if len(detected_folders) > 1:
                # Monorepo detected — ask which part(s) to deploy
                options = "\n".join(
                    f"  {i+1}. {folder_name.upper()} ({config['stack']}, port {config['default_port']})"
                    for i, (folder_name, config) in enumerate(detected_folders.items())
                )
                options += f"\n  {len(detected_folders)+1}. ALL PARTS (deploy all)"
                
                raise NeedsInputError(
                    f"Monorepo detected with multiple deployment targets:\n\n{options}\n\n"
                    f"Which part(s) should I deploy?\n"
                    f"(Enter number, e.g., '1', '2', '3', or 'both'/'all')",
                    {
                        "step": "choose_monorepo_parts",
                        "detected_folders": detected_folders,
                        "ctx_partial": {
                            "github_url": ctx.github_url,
                            "stack": ctx.stack,
                            "port": ctx.port,
                            "domain": ctx.domain,
                            "process_manager": ctx.process_manager,
                            "branch": ctx.branch,
                            "app_type": ctx.app_type,
                            "app_name": ctx.app_name,
                            "app_path": ctx.app_path,
                        },
                        "prefill": pf,
                        "agent": "deployment",
                    }
                )
            
            elif len(detected_folders) == 1:
                # Single deployable folder found — update context to use it
                folder_name = list(detected_folders.keys())[0]
                folder_config = detected_folders[folder_name]
                
                logger.info(f"[MONOREPO] Single deployable folder detected: {folder_name} ({folder_config['stack']})")
                
                # Update context to deploy this specific folder
                ctx.app_path = f"{ctx.app_path}/{folder_name}".rstrip("/")
                
                # Update stack if different from what LLM detected
                # (in case LLM guess was wrong)
                if folder_config['stack'] in DeploymentContext.FRONTEND_STACKS or folder_config['stack'] == 'nextjs':
                    ctx.app_type = "frontend"
                elif folder_config['stack'] in DeploymentContext.BACKEND_STACKS:
                    ctx.app_type = "backend"
                
                logger.info(f"[MONOREPO] Updated deployment path to: {ctx.app_path}")
        
        except NeedsInputError:
            # Re-raise monorepo choice questions
            raise
        except Exception as e:
            # Log but don't fail on monorepo detection — user may have provided explicit path
            logger.warning(f"[MONOREPO DETECTION] Error scanning for monorepo: {e}")

        # ── Handle monorepo deployment (if user selected multiple parts) ─────
        pf = getattr(self, '_prefill', None) or {}
        if pf.get("deploy_monorepo") and pf.get("selected_monorepo_parts"):
            # User chose to deploy multiple parts of monorepo
            detected_folders = pf.get("detected_folders", {})
            selected_parts = pf.get("selected_monorepo_parts", [])
            
            logger.info(f"[MONOREPO] Deploying {len(selected_parts)} part(s): {selected_parts}")
            
            # Deploy each part sequentially with incrementing ports
            base_port = int(ctx.port)
            for i, part_name in enumerate(selected_parts):
                if part_name not in detected_folders:
                    logger.warning(f"[MONOREPO] Skipping unknown part: {part_name}")
                    continue
                
                config = detected_folders[part_name]
                current_port = base_port + (i * 1000)  # Each part gets port+1000, port+2000, etc.
                
                logger.info(f"[MONOREPO] Deploying part {i+1}/{len(selected_parts)}: {part_name} (port {current_port})")
                
                # Create deployment context for this part
                part_ctx = DeploymentContext(
                    github_url=ctx.github_url,
                    stack=config["stack"],
                    port=str(current_port),
                    app_path=f"{ctx.app_path}/{part_name}".rstrip("/"),
                    app_type="frontend" if config["stack"] in DeploymentContext.FRONTEND_STACKS or config["stack"] == "nextjs" else "backend",
                    process_manager=ctx.process_manager,
                    domain=ctx.domain,
                    branch=ctx.branch,
                    env_vars=ctx.env_vars.copy() if ctx.env_vars else {},
                )
                part_ctx.app_name = f"{ctx.app_name}-{part_name}"
                
                try:
                    # Write .env if needed
                    if part_ctx.env_vars:
                        self._step_write_env(part_ctx)
                    
                    # Install and start
                    self._step_install(part_ctx)
                    results.append(f"✓ {part_name} deployed on port {current_port}")
                    
                except Exception as e:
                    logger.error(f"[MONOREPO PART FAILED] {part_name}: {e}")
                    self.alerter.critical(
                        title=f"Monorepo deployment FAILED for part: {part_name}",
                        server=self._server,
                        details=str(e)[:300],
                    )
                    results.append(f"✗ {part_name} FAILED: {str(e)[:100]}")
                    # Continue to next part instead of aborting
                    continue
            
            # After deploying all parts, move to nginx config
            # Use the main ctx (frontend port) for nginx
            try:
                self._step_nginx(ctx)
                results.append("✓ nginx")
            except Exception as e:
                from app.services.conversation_service import NeedsInputError
                if isinstance(e, NeedsInputError):
                    raise
                logger.error(f"[MONOREPO NGINX FAILED] {e}")
                results.append(f"⚠ nginx: {str(e)[:60]}")
            
            # Return summary
            summary = "MONOREPO DEPLOYMENT COMPLETE\n"
            summary += f"Parts deployed: {len(selected_parts)}\n"
            summary += "\n".join(results)
            return summary

        # ── Step 2: Write .env (only if vars collected) ────────────────────
        if ctx.env_vars:
            try:
                self._step_write_env(ctx)
                results.append(f"✓ env_file ({len(ctx.env_vars)} vars)")
            except Exception as e:
                logger.error(f"[STEP 2 FAILED] {e}")
                self.alerter.critical(
                    title=f"Deployment FAILED: {ctx.app_name} — .env write error",
                    server=self._server,
                    details=str(e)[:300],
                )
                return f"DEPLOYMENT FAILED writing .env.\n{e}"
        else:
            results.append("- env_file: skipped")

        # ── Step 3: Install + start process ───────────────────────────────
        try:
            self._step_install(ctx)
            results.append(f"✓ install ({ctx.stack} via {ctx.process_manager})")
        except Exception as e:
            from app.services.conversation_service import NeedsInputError
            if isinstance(e, NeedsInputError):
                # Augment the error with full deployment context for proper resumption
                if not e.state:
                    e.state = {}
                if "ctx_partial" not in e.state:
                    e.state["ctx_partial"] = {
                        "github_url": ctx.github_url,
                        "stack": ctx.stack,
                        "port": ctx.port,
                        "domain": ctx.domain,
                        "process_manager": ctx.process_manager,
                        "branch": ctx.branch,
                        "app_type": ctx.app_type,
                        "app_name": ctx.app_name,
                        "app_path": ctx.app_path,
                    }
                raise
            
            logger.error(f"[STEP 3 FAILED] {e}")
            err_str = str(e)
            if "no space left" in err_str.lower() or "errno 28" in err_str.lower():
                self.alerter.critical(
                    title=f"Deployment FAILED: {ctx.app_name} — DISK FULL",
                    server=self._server,
                    details=f"Disk is full. Run MaintenanceAgent.clear_disk() to free space.\n{err_str[:200]}",
                )
            else:
                self.alerter.critical(
                    title=f"Deployment FAILED: {ctx.app_name} — install error",
                    server=self._server,
                    details=err_str[:300],
                )
            return f"DEPLOYMENT FAILED at install.\n{e}"

        # ── Step 4: Nginx ──────────────────────────────────────────────────
        # Mark that clone+install completed — used by nginx-only fast-path on resume
        if hasattr(self, '_prefill') and isinstance(self._prefill, dict):
            self._prefill['deploy_steps_done'] = True
        try:
            self._step_nginx(ctx)
            results.append("✓ nginx")
        except Exception as e:
            from app.services.conversation_service import NeedsInputError
            if isinstance(e, NeedsInputError):
                # Augment the error with full deployment context for proper resumption
                if not e.state:
                    e.state = {}
                if "ctx_partial" not in e.state:
                    e.state["ctx_partial"] = {
                        "github_url": ctx.github_url,
                        "stack": ctx.stack,
                        "port": ctx.port,
                        "domain": ctx.domain,
                        "process_manager": ctx.process_manager,
                        "branch": ctx.branch,
                        "app_type": ctx.app_type,
                        "app_name": ctx.app_name,
                        "app_path": ctx.app_path,
                        "deploy_steps_done": True,  # clone+install already done
                        "env_vars": ctx.env_vars,
                    }
                raise  # let the API layer catch it and ask the user
            logger.error(f"[STEP 4 FAILED] {e}")
            self.alerter.critical(
                title=f"Deployment FAILED: {ctx.app_name} — nginx error",
                server=self._server,
                details=str(e)[:300],
            )
            return f"DEPLOYMENT FAILED at nginx.\n{e}"

        # ── Success ────────────────────────────────────────────────────────
        summary = "\n".join(results)
        
        # Check if this was a redeploy
        is_redeploy = pf.get("redeploy", False)
        operation_type = "REDEPLOY" if is_redeploy else "DEPLOYMENT"
        
        self.alerter.info(
            title=f"{operation_type} SUCCESS: {ctx.app_name}",
            server=self._server,
            details=f"URL: http://{ctx.domain} | Port: {ctx.port} | Stack: {ctx.stack}",
        )
        
        status_header = f"{operation_type} COMPLETE"
        if is_redeploy:
            status_header += f"\n✓ Pulled latest from branch: {pf.get('current_branch', 'main')}"
        
        return (
            f"{status_header}\n"
            f"App:    {ctx.app_name}\n"
            f"URL:    http://{ctx.domain}\n"
            f"Port:   {ctx.port}\n"
            f"Stack:  {ctx.stack}\n\n"
            + summary
        )
