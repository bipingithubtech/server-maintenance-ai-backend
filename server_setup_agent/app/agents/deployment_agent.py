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
  port        - integer port the app server listens on (NOT 80 or 443)
  domain      - domain name or IP address for nginx

Optional:
  env_vars    - object with KEY: "value" pairs if the app needs a .env file (empty object {} if not needed)

Rules:
- If the user message contains all required fields, return JSON immediately.
- If any required field is missing, return JSON with a "missing" array listing what is missing and a "question" string asking for all missing fields at once.
- NEVER guess port. If not provided, list it as missing.
- Return ONLY valid JSON. No markdown, no explanation.

Examples:

User: "deploy https://github.com/Org/repo.git stack=nextjs port=5006 domain=192.168.1.10 no-env"
Response: {"github_url":"https://github.com/Org/repo.git","stack":"nextjs","port":"5006","domain":"192.168.1.10","env_vars":{}}

User: "deploy https://github.com/Org/repo.git stack=nextjs"
Response: {"missing":["port","domain"],"question":"What port does the app run on, and what domain or IP should nginx use?"}

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

    # ── Phase 1: gather context via LLM ───────────────────────────────────────

    def _gather_context(self, query: str) -> DeploymentContext:
        """
        Single LLM call to extract all required deployment info.
        If anything is missing, prints the LLM's question and prompts the user.
        Loops until all required fields are collected.
        Saves final context to deployment_context.json before returning.
        """
        messages = [
            SystemMessage(content=_GATHER_SYSTEM),
            HumanMessage(content=query),
        ]

        while True:
            time.sleep(1)
            response = self.llm.invoke(messages)
            raw = response.content.strip()

            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # LLM returned non-JSON — ask again
                messages.append(response)
                messages.append(HumanMessage(content="Please respond with valid JSON only."))
                continue

            # Missing fields — ask the user
            if "missing" in data:
                print(f"\n[?] {data.get('question', 'Please provide the missing information.')}")
                user_answer = input("    Your answer: ").strip()
                messages.append(response)
                messages.append(HumanMessage(content=user_answer))
                continue

            # All fields present — build context
            ctx = DeploymentContext(
                github_url = data["github_url"],
                stack      = data["stack"].lower(),
                port       = str(data["port"]),
                domain     = data["domain"],
                env_vars   = data.get("env_vars", {}),
            )

            # ── Ask for process manager ────────────────────────────────────
            stack = ctx.stack
            if stack in ("react", "vite", "angular"):
                # Static apps — no process manager needed
                ctx.process_manager = "none"
            else:
                print("\n" + "─" * 60)
                print("  Process Manager")
                print("─" * 60)
                if stack in ("fastapi", "flask", "django"):
                    options = "pm2 | systemd | docker"
                    default = "systemd"
                else:
                    options = "pm2 | systemd | docker"
                    default = "pm2"
                pm_answer = input(
                    f"\n[?] How should the app be managed? ({options})\n"
                    f"    Press Enter for default [{default}]: "
                ).strip().lower()
                ctx.process_manager = pm_answer if pm_answer in ("pm2", "systemd", "docker") else default
                print(f"  Using: {ctx.process_manager}")
                print("─" * 60)

            # ── Mandatory .env collection ──────────────────────────────────
            # Always ask — never skip unless user explicitly says no.
            # If .env.example exists on the repo, clone it first to read var names.
            # We do a lightweight check via the GitHub raw URL.
            print("\n" + "─" * 60)
            print("  .env Configuration")
            print("─" * 60)

            # Try to fetch .env.example from GitHub to show required vars
            env_example_vars = []
            try:
                import urllib.request
                parts = ctx.github_url.replace("https://github.com/", "").replace(".git", "").split("/")
                raw_url = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/HEAD/.env.example"
                req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    example_content = resp.read().decode()
                for line in example_content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=")[0].strip()
                        env_example_vars.append(key)
                if env_example_vars:
                    print(f"  Found .env.example with {len(env_example_vars)} variable(s):")
                    print(f"  {', '.join(env_example_vars)}")
            except Exception:
                pass  # no .env.example or no network — proceed without it

            needs_env = input("\n[?] Does this app need a .env file? (yes/no): ").strip().lower()

            if needs_env.startswith("y") or needs_env.startswith("1"):
                if env_example_vars:
                    print(f"\n  Variables from .env.example ({len(env_example_vars)} required):")
                    extra_raw = input(
                        "  Add extra variable names? (comma-separated, or press Enter to skip): "
                    ).strip()
                    if extra_raw:
                        env_example_vars += [v.strip() for v in extra_raw.split(",") if v.strip()]
                else:
                    raw_keys = input(
                        "  Enter ALL variable names (comma-separated):\n  > "
                    ).strip()
                    env_example_vars = [k.strip() for k in raw_keys.split(",") if k.strip()]

                if not env_example_vars:
                    print("  No variables entered — skipping .env.")
                else:
                    print(f"\n  Enter value for each variable (required — press Enter to leave empty):")
                    filled = {}
                    for key in env_example_vars:
                        # Show existing value from LLM parse if present
                        existing = data.get("env_vars", {}).get(key, "")
                        prompt = f"    {key}={f'[{existing}] ' if existing else ''}"
                        val = input(prompt).strip()
                        # Use existing if user just pressed Enter
                        filled[key] = val if val else existing
                    ctx.env_vars = filled
                    print(f"\n  ✓ Collected {len(ctx.env_vars)} env var(s).")
            else:
                ctx.env_vars = {}
                print("  Skipping .env file.")

            print("─" * 60)

            # Save context to disk so steps can reference it
            self._save_context(ctx)

            logger.info(
                f"[CONTEXT] app={ctx.app_name} stack={ctx.stack} "
                f"port={ctx.port} domain={ctx.domain} "
                f"env_vars={list(ctx.env_vars.keys())}"
            )
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
        self._run(
            f"git clone https://$(cat ~/.github_token)@"
            f"{ctx.github_url.replace('https://', '')} {ctx.app_path}"
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
        # Check Dockerfile exists
        if not self._file_exists(f"{ctx.app_path}/Dockerfile"):
            raise RuntimeError(
                f"No Dockerfile found at {ctx.app_path}. "
                "Cannot deploy with Docker."
            )
        # Install Docker if needed
        code, _, _ = self.executor.execute("which docker")
        if code != 0:
            self._run("sudo apt-get update -y && sudo apt-get install -y docker.io")
            self._run("sudo systemctl start docker && sudo systemctl enable docker")
            self._run(f"sudo usermod -aG docker $(whoami)")

        # Stop existing container
        self.executor.execute(f"docker stop {ctx.app_name} 2>/dev/null || true")
        self.executor.execute(f"docker rm {ctx.app_name} 2>/dev/null || true")

        # Build image
        self._run(f"docker build -t {ctx.app_name} {ctx.app_path}")

        # Run container
        env_flags = " ".join(f"-e {k}={v}" for k, v in ctx.env_vars.items())
        self._run(
            f"docker run -d --name {ctx.app_name} "
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
