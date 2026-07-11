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


# ── Deployment context ─────────────────────────────────────────────────────────

@dataclass
class DeploymentContext:
    github_url       : str
    stack            : str
    port             : str
    domain           : str
    env_vars         : Dict[str, str] = field(default_factory=dict)
    process_manager  : str = ""   # pm2 | systemd | docker
    branch           : str = "main"  # branch to clone

    # Derived — filled automatically
    app_name   : str = ""
    app_path   : str = ""

    def __post_init__(self):
        if not self.github_url.endswith(".git"):
            self.github_url += ".git"
        repo = self.github_url.rstrip("/").split("/")[-1].replace(".git", "")
        self.app_name = repo.lower().replace("_", "-")
        self.app_path = f"/opt/{self.app_name}"



_GATHER_SYSTEM = """You are a deployment assistant. 
Your ONLY job is to extract deployment info from the user message and return it as JSON.

Required fields:
  github_url  - full GitHub HTTPS URL (must start with https://github.com/)
  stack       - one of: react, vite, angular, nextjs, nodejs, nestjs, fastapi, flask, django
                IMPORTANT: "nextjs" = Next.js (frontend framework). "nestjs" = NestJS (backend Node framework).
                If user says "next js" for a backend repo, clarify — they likely mean "nestjs".
  port        - the internal port the app process listens on (NOT 80 or 443 — nginx always listens on 80).
                For backend stacks (fastapi, flask, django, nodejs, nestjs): this is the app server port
                  e.g. uvicorn port 8001 → nginx proxies 80 → localhost:8001
                For frontend stacks (react, vite, angular): this is the Vite/webpack dev server port
                  (nginx serves static files from /dist, so port is not used in the nginx config,
                   but still collect it in case the user needs to run the dev server)

Optional fields (do NOT ask for these if not provided — they can be filled automatically):
  domain      - domain name or IP address for nginx server_name. If not provided, return "".

Optional:
  env_vars    - object with KEY: "value" pairs if the app needs a .env file (empty object {} if not needed)

Rules:
- If the user message contains github_url, stack, and port, return JSON immediately — domain is optional.
- If github_url, stack, or port is missing, return JSON with a "missing" array and a "question" string.
- NEVER list "domain" as missing — it is optional and will be filled automatically from the server connection.
- NEVER guess port. If not provided, list it as missing.
- Return ONLY valid JSON. No markdown, no explanation.

Examples:

User: "deploy https://github.com/Org/repo.git stack=nextjs port=5006 domain=192.168.1.10 no-env"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"nextjs","port":"5006","domain":"192.168.1.10","env_vars":{}}

User: "deploy https://github.com/Org/repo.git stack=nextjs port=5006"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"nextjs","port":"5006","domain":"","env_vars":{}}

User: "deploy https://github.com/Org/repo.git stack=nextjs"
Response: {"missing":["port"],"question":"What port does the app run on internally? (nginx will proxy port 80 to this)"}

User: "deploy https://github.com/Org/repo.git stack=fastapi port=8000 domain=myapp.com env DATABASE_URL=postgres://localhost/db SECRET_KEY=abc123"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"fastapi","port":"8000","domain":"myapp.com","env_vars":{"DATABASE_URL":"postgres://localhost/db","SECRET_KEY":"abc123"}}
"""


# ── Agent ──────────────────────────────────────────────────────────────────────

class DeploymentAgent:

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}
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

        # ── Process manager ────────────────────────────────────────────
        stack = ctx.stack
        if pf.get("process_manager"):
            ctx.process_manager = pf["process_manager"]
        else:
            if stack in ("fastapi", "flask", "django"):
                default = "systemd"
            elif stack in ("react", "vite", "angular"):
                default = "pm2"  # serve built dist via PM2 + npx serve
            else:
                default = "pm2"
            raise NeedsInputError(
                f"How should the app be managed? Options: pm2 | systemd | docker  (recommended: {default})",
                {"step": "need_process_manager", "ctx_partial": {
                    "github_url": ctx.github_url, "stack": ctx.stack,
                    "port": ctx.port, "domain": ctx.domain,
                }, "prefill": pf, "agent": "deployment", "default_pm": default}
            )

        # ── Branch selection ───────────────────────────────────────────
        if pf.get("branch"):
            ctx.branch = pf["branch"]
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
                    }, "prefill": pf, "agent": "deployment"}
                )
        else:
            self._github_token = github_token

        # ── .env vars ──────────────────────────────────────────────────
        if pf.get("env_vars") is not None:
            ctx.env_vars = pf["env_vars"]
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
                }, "env_example_vars": env_example_vars, "prefill": pf, "agent": "deployment"}
            )

        self._save_context(ctx)
        logger.info(f"[CONTEXT] app={ctx.app_name} stack={ctx.stack} port={ctx.port} branch={ctx.branch}")
        return ctx

    def _save_context(self, ctx: DeploymentContext) -> None:
        """Saves deployment context to deployment_context.json."""
        data = {
            "github_url":      ctx.github_url,
            "stack":           ctx.stack,
            "port":            ctx.port,
            "domain":          ctx.domain,
            "app_name":        ctx.app_name,
            "app_path":        ctx.app_path,
            "env_vars":        ctx.env_vars,
            "process_manager": ctx.process_manager,
            "branch":          ctx.branch,
        }
        with open("deployment_context.json", "w") as f:
            json.dump(data, f, indent=2)
        logger.info("[CONTEXT] Saved to deployment_context.json")

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
        self._run(
            f"sudo mkdir -p {ctx.app_path} && "
            f"sudo chown -R $(whoami):$(id -gn) {ctx.app_path}"
        )

        # Build authenticated clone URL.
        # Priority: 1) token passed from frontend/config
        #           2) ~/.github_token file on the remote server
        #           3) plain HTTPS (public repos only)
        from app.core.config import settings
        token = getattr(self, '_github_token', None) or settings.GITHUB_TOKEN

        if not token:
            # Try reading ~/.github_token from the remote server
            result = self._inspect("cat ~/.github_token 2>/dev/null")
            if result.strip() and "COMMAND DID NOT SUCCEED" not in result:
                token = result.strip()

        repo_path = ctx.github_url.replace("https://", "")
        if token:
            clone_url = f"https://{token}@{repo_path}"
        else:
            logger.warning(
                "No GitHub token found. Attempting unauthenticated clone — "
                "this will fail for private repos. "
                "Add token to ~/.github_token on the server or set GITHUB_TOKEN in .env"
            )
            clone_url = ctx.github_url

        self._run(
            f"GIT_TERMINAL_PROMPT=0 git clone --branch {ctx.branch} --single-branch "
            f"{clone_url} {ctx.app_path}"
        )
        self._run(f"sudo chown -R $(whoami):$(id -gn) {ctx.app_path}")
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

        # ── Static (no process manager) ────────────────────────────────────
        if stack in ("react", "vite", "angular"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            self._run(f"npm run build --prefix {app_path}")
            return

        # ── Node / Next.js / NestJS ────────────────────────────────────────
        if stack in ("nextjs", "nodejs", "nestjs"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            if stack in ("nextjs", "nestjs"):
                self._run(f"npm run build --prefix {app_path}")
            entry = "npm" if stack == "nextjs" else ("dist/main.js" if stack == "nestjs" else "index.js")

            if pm == "pm2":
                self.pm2.install()
                self.pm2.start(app_name=app_name, script=entry, working_directory=app_path)
                self.pm2.save()
                ctx.port = self._detect_actual_port(app_name, ctx.port)

            elif pm == "systemd":
                if stack == "nextjs":
                    exec_start = f"/usr/bin/npm --prefix {app_path} run start"
                else:
                    exec_start = f"/usr/bin/node {app_path}/{entry}"
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

        # Handle case-insensitive Dockerfile names (repo has 'DockerFile' not 'Dockerfile')
        # Normalize to lowercase so docker build works consistently
        self.executor.execute(
            f"test -f {ctx.app_path}/DockerFile "
            f"&& mv {ctx.app_path}/DockerFile {ctx.app_path}/Dockerfile 2>/dev/null "
            f"|| true"
        )

        # Auto-generate Dockerfile if still missing but repo has requirements.txt/package.json
        if not self._file_exists(f"{ctx.app_path}/Dockerfile"):
            has_requirements = self._file_exists(f"{ctx.app_path}/requirements.txt")
            has_package_json = self._file_exists(f"{ctx.app_path}/package.json")
            if has_requirements or has_package_json:
                logger.info(f"  [DOCKER] No Dockerfile found — auto-generating for {ctx.stack}")
                self._generate_dockerfile(ctx)
            else:
                raise RuntimeError(
                    f"No Dockerfile found at {ctx.app_path} and no requirements.txt/package.json "
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
        env_flags = " ".join(f"-e {k}={v}" for k, v in ctx.env_vars.items())
        self._run(
            f"sudo docker run -d --name {ctx.app_name} "
            f"--restart unless-stopped "
            f"-p {ctx.port}:{ctx.port} "
            f"{env_flags} "
            f"{ctx.app_name}"
        )
        logger.info(f"  [DOCKER] Container {ctx.app_name} running on port {ctx.port}")

    def _check_port_conflict(self, port: str) -> None:
        """
        Checks if the given port is already in use on the server.
        If occupied, shows what process is using it and asks the user to confirm or change.
        Updates ctx.port if user provides a different port.
        Returns the confirmed port.
        """
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
        print(f"\n  ⚠ Port {port} is already occupied by: {process_name or 'unknown'} (pid={pid or '?'})")
        print(f"  Full ss output: {ss_out.strip()}")

        answer = input(
            f"\n[?] Port {port} is in use. Options:\n"
            f"    1. Enter a different port number\n"
            f"    2. Press Enter to continue anyway (app may override it from .env)\n"
            f"    Your choice: "
        ).strip()

        if answer.isdigit():
            logger.info(f"  [PORT] User switched port from {port} to {answer}")
            return answer

        logger.info(f"  [PORT] User chose to continue with port {port} despite conflict.")
        return port

    def _detect_actual_port(self, app_name: str, expected_port: str) -> str:
        """After PM2 start, read actual port from logs and ss."""
        import time as _t
        _t.sleep(3)

        _, logs, _ = self.executor.execute(
            f"pm2 logs {app_name} --lines 30 --nostream --no-color 2>/dev/null"
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
        stack    = ctx.stack
        app_path = ctx.app_path
        app_name = ctx.app_name
        port     = ctx.port
        pm       = ctx.process_manager

        if stack in ("react", "vite", "angular"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            self._run(f"npm run build --prefix {app_path}")
            return

        if stack in ("nextjs", "nodejs", "nestjs"):
            self.pkg.install("nodejs")
            self._run(f"npm install --prefix {app_path}")
            if stack in ("nextjs", "nestjs"):
                self._run(f"npm run build --prefix {app_path}")
            entry = "npm" if stack == "nextjs" else ("dist/main.js" if stack == "nestjs" else "index.js")

            if pm == "pm2":
                self.pm2.install()
                self.pm2.start(app_name=app_name, script=entry, working_directory=app_path)
                self.pm2.save()
                ctx.port = self._detect_actual_port(app_name, ctx.port)
            elif pm == "systemd":
                exec_start = (
                    f"/usr/bin/npm --prefix {app_path} run start"
                    if stack == "nextjs"
                    else f"/usr/bin/node {app_path}/{entry}"
                )
                self.systemd.create_service_file(
                    service_name=app_name,
                    exec_start=exec_start,
                    working_directory=app_path,
                )
                self.systemd.start_service(app_name)
                self.systemd.enable_service(app_name)
            elif pm == "docker":
                self._deploy_docker(ctx)

        elif stack in ("fastapi", "flask", "django"):
            if not self._file_exists(f"{app_path}/requirements.txt"):
                raise RuntimeError(f"No requirements.txt found at {app_path}.")

            if pm == "docker":
                self._deploy_docker(ctx)
                return

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
                self.pm2.start(app_name=app_name, script=exec_cmd.split()[0], working_directory=app_path)
                self.pm2.save()
            else:
                self.systemd.create_service_file(
                    service_name=app_name,
                    exec_start=exec_cmd,
                    working_directory=app_path,
                )
                self.systemd.start_service(app_name)
                self.systemd.enable_service(app_name)
        else:
            raise RuntimeError(f"Unsupported stack: {stack}")

    def _step_nginx(self, ctx: DeploymentContext) -> None:
        logger.info("[STEP 4/5] Nginx")
        self.nginx.install()

        if ctx.stack in ("react", "vite", "angular"):
            # Find the static dist folder
            dist_path = ctx.app_path + "/dist"
            for candidate in (".next", "dist", "build", "out"):
                if self._dir_exists(f"{ctx.app_path}/{candidate}"):
                    dist_path = f"{ctx.app_path}/{candidate}"
                    break
            self.nginx.generate_and_save_config(
                framework=ctx.stack,
                domain=ctx.domain,
                app_name=ctx.app_name,
                app_path=dist_path,
            )
        else:
            self.nginx.generate_and_save_config(
                framework=ctx.stack,
                domain=ctx.domain,
                app_name=ctx.app_name,
                port=int(ctx.port),
            )

        self.nginx.test_config()
        self.nginx.enable_site(ctx.app_name)
        self.nginx.reload_nginx()

    # ── Public entry point ─────────────────────────────────────────────────────

    def execute_task(self, query: str) -> str:
        # ── Phase 1: collect all required info via one LLM call ───────────
        logger.info("[PHASE 1] Gathering deployment context via LLM...")
        ctx = self._gather_context(query)

        logger.info(
            f"[PHASE 2] Starting deployment: {ctx.app_name} | "
            f"stack={ctx.stack} | port={ctx.port} | domain={ctx.domain}"
        )

        # Check port conflict before touching the server
        ctx.port = self._check_port_conflict(ctx.port)

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

        # ── Step 1: Clone ──────────────────────────────────────────────────
        try:
            self._step_clone(ctx)
            results.append("✓ clone")
        except Exception as e:
            logger.error(f"[STEP 1 FAILED] {e}")
            self.alerter.critical(
                title=f"Deployment FAILED: {ctx.app_name} — clone error",
                server=self._server,
                details=str(e)[:300],
            )
            return f"DEPLOYMENT FAILED at clone.\n{e}"

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
        try:
            self._step_nginx(ctx)
            results.append("✓ nginx")
        except Exception as e:
            logger.error(f"[STEP 4 FAILED] {e}")
            self.alerter.critical(
                title=f"Deployment FAILED: {ctx.app_name} — nginx error",
                server=self._server,
                details=str(e)[:300],
            )
            return f"DEPLOYMENT FAILED at nginx.\n{e}"

        # ── Success ────────────────────────────────────────────────────────
        summary = "\n".join(results)
        self.alerter.info(
            title=f"Deployment SUCCESS: {ctx.app_name}",
            server=self._server,
            details=f"URL: http://{ctx.domain} | Port: {ctx.port} | Stack: {ctx.stack}",
        )
        return (
            f"DEPLOYMENT COMPLETE\n"
            f"App:    {ctx.app_name}\n"
            f"URL:    http://{ctx.domain}\n"
            f"Port:   {ctx.port}\n"
            f"Stack:  {ctx.stack}\n\n"
            + summary
        )
