"""
SetupAgent — Server initial setup with one LLM call.

Phase 1 (ONE LLM call):
  - Understands what the user wants to set up
  - Shows the plan (what will be installed/configured)
  - Asks if they want to add anything else
  - Saves the setup plan to setup_context.json

Phase 2 (Direct Python — no LLM):
  - Executes each step directly using tool classes
  - No LangGraph loops, no token waste

Supported setup tasks:
  - base           : apt update + upgrade + essential packages
  - nginx          : install + start nginx
  - docker         : install + start docker
  - nodejs         : install nodejs + npm
  - pm2            : install pm2 globally
  - python         : install python3 + pip + venv
  - firewall       : UFW setup (deny all, allow SSH/80/443 as relevant + custom ports)
  - fail2ban       : SSH brute-force protection
  - ssh_harden     : disable root login, key-only auth (production-safe default), set MaxAuthTries
  - bootstrap_user : create a new sudo user with SSH key access (root-only fresh server flow)
  - auto_updates   : enable unattended-upgrades for automatic security patches
  - custom         : any additional packages/commands the user specifies
"""

import json
import re
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from loguru import logger

from app.services.llm_service import get_llm
from app.services.teams_alert_service import TeamsAlerter
from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.package_tool import PackageTool
from app.tools.nginx_tool import NginxTool
from app.tools.firewall_tool import FirewallTool
from app.tools.security_tool import SecurityTool
from app.tools.docker_tool import DockerTool
from app.tools.pm2_tool import PM2Tool
from app.tools.ssh_tool import SSHTool


# ── Setup context ──────────────────────────────────────────────────────────────

@dataclass
class SetupContext:
    tasks:            List[str]
    infra_services:   List[str]
    extra_packages:   List[str]
    extra_commands:   List[str]
    firewall_ports:   List[str]
    open_ports:       List[str]
    suggestions:      List[str] = field(default_factory=list)
    server_purpose:   str = ""
    new_username:     str = ""        # used by bootstrap_user task
    new_user_password:str = ""        # password for the new user (not stored to disk)
    your_public_key:  str = ""        # optional SSH public key for the new user


# ── LLM gather prompt ──────────────────────────────────────────────────────────

_GATHER_SYSTEM = """You are a server setup assistant.
Your ONLY job: extract what the user wants to set up and return JSON.

Available setup tasks:
  base             - apt update/upgrade + essential tools (curl, git, wget, unzip, build-essential)
  nginx            - install and start Nginx web server
  docker           - install and start Docker
  nodejs           - install Node.js and npm
  pm2              - install PM2 process manager (requires nodejs)
  python           - install Python3, pip, venv
  firewall         - configure UFW (deny all, allow SSH/80/443 as relevant + custom ports)
  fail2ban         - SSH brute-force protection
  ssh_harden       - disable root SSH login, key-only auth (production-safe default), set MaxAuthTries
  bootstrap_user   - create a new sudo user with SSH key access (run when connected as root on a fresh server)
  auto_updates     - enable unattended-upgrades for automatic security patches

Infrastructure services (run as shared Docker containers — one per server):
  redis      - Redis cache server (port 6379) — shared by all apps
  postgres   - PostgreSQL database (port 5432) — shared by all apps, each app uses different DB name
  mysql      - MySQL database (port 3306)
  mongodb    - MongoDB (port 27017)

JSON fields:
  tasks          - array of task names from the list above
  infra_services - array of infrastructure services: ["redis", "postgres", "mysql", "mongodb"]
  extra_packages - array of additional apt packages to install (e.g. ["htop", "vim"])
  extra_commands - array of custom shell commands to run after setup
  firewall_ports - array of port numbers to open in UFW (e.g. ["3000", "8080"]) — ONLY if explicitly requested
  server_purpose - short description of what this server is for
  new_username   - if user wants a new sudo user created (bootstrap_user task), the desired username; else ""
  suggestions    - array of strings: things the user likely needs but didn't mention
                   Examples:
                   - "pm2 not included — required to keep Node.js apps running after reboot"
                   - "fail2ban not included — recommended to protect SSH from brute-force attacks"
                   - "docker not included but redis/postgres requested — docker is required to run infra containers"
                   - "firewall not included — recommended to block unused ports"
                   - "ssh_harden not included — recommended to disable root login and enforce key-only SSH"
                   - "auto_updates not included — recommended so the server keeps patching itself"
                   - "if connecting as root on a fresh server, consider bootstrap_user to create a sudo user before hardening SSH"
                   Only suggest things that are genuinely missing and useful. Keep suggestions concise.

Rules:
- If user mentions redis/postgres/mysql/mongodb: put in infra_services ONLY — NEVER put them in tasks
- infra_services always require docker task to be included
- If user mentions "root", "fresh server", or "create a user": include bootstrap_user and ask for new_username if not given
- If user says "full setup": include all tasks + common infra (redis, postgres)
- NEVER guess firewall_ports unless user explicitly mentions them
- bootstrap_user should run BEFORE ssh_harden in practice (handled by execution order, not by you)
- NEVER include infra service names (redis, postgres, mysql, mongodb) in tasks — they only belong in infra_services
- Return ONLY valid JSON

Examples:

User: "setup a fresh server as root, create a sudo user called deploy, then harden it"
Response: {"tasks":["base","bootstrap_user","ssh_harden","fail2ban","firewall","auto_updates"],"infra_services":[],"extra_packages":[],"extra_commands":[],"firewall_ports":[],"server_purpose":"hardened fresh server","new_username":"deploy"}

User: "setup a web server with nginx and docker"
Response: {"tasks":["base","nginx","docker","firewall"],"infra_services":[],"extra_packages":[],"extra_commands":[],"firewall_ports":[],"server_purpose":"web server","new_username":""}

User: "full server setup with nodejs pm2 and redis"
Response: {"tasks":["base","nginx","nodejs","pm2","docker","firewall","fail2ban","ssh_harden","auto_updates"],"infra_services":["redis"],"extra_packages":[],"extra_commands":[],"firewall_ports":[],"server_purpose":"Node.js app server with Redis","new_username":""}

User: "setup server with postgres and redis for python backend"
Response: {"tasks":["base","python","docker","firewall"],"infra_services":["redis","postgres"],"extra_packages":[],"extra_commands":[],"firewall_ports":[],"server_purpose":"Python backend with Redis and Postgres","new_username":""}

User: "missing something"
Response: {"missing":true,"question":"What would you like to set up? E.g. web server (nginx), Node.js app, Python app, Docker, Redis, Postgres, full setup, fresh server bootstrap, etc."}
"""


# ── Agent ──────────────────────────────────────────────────────────────────────

class SetupAgent:

    def __init__(
        self,
        executor_type:   str = "local",
        executor_config: Dict[str, Any] = None,
        server_label:    Optional[str] = None,
        agent_ignore_ips: str = "",
    ):
        """
        agent_ignore_ips: space-separated IP(s)/CIDR(s) of the machine(s) this
        agent itself connects FROM (e.g. your dev box's host-only adapter IP).
        Passed through to fail2ban's jail config so the agent's own reconnect
        attempts are never mistaken for brute-force and banned.
        """
        if executor_config is None:
            executor_config = {}

        self.executor     = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.llm          = get_llm()
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")
        self.agent_ignore_ips = agent_ignore_ips

        # Tool instances
        self.linux    = LinuxTool(self.executor)
        self.pkg      = PackageTool(self.executor)
        self.nginx    = NginxTool(self.executor)
        self.firewall = FirewallTool(self.executor)
        self.security = SecurityTool(self.executor)
        self.docker   = DockerTool(self.executor)
        self.pm2      = PM2Tool(self.executor)
        self.ssh_tool = SSHTool(self.executor)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _run(self, cmd: str) -> str:
        logger.info(f"  [RUN] {cmd[:160]}")
        result = self.linux.run_custom_command(cmd)
        logger.info(f"  [OK]  {str(result)[:120]}")
        return result

    def _exec(self, cmd: str):
        _, out, err = self.executor.execute(cmd)
        return out.strip(), err.strip()

    def _generate_password(self, length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    # ── Server scan — check what's already installed ───────────────────────────

    def _scan_server(self) -> Dict[str, Any]:
        """
        Scan the server and return what's already installed with versions.
        Used to skip tasks that are already done.
        """
        installed = {}

        checks = {
            "base":     ("curl --version 2>/dev/null | head -1", "curl"),
            "nginx":    ("nginx -v 2>&1 | head -1",              "nginx"),
            "docker":   ("docker --version 2>/dev/null",          "Docker"),
            "nodejs":   ("node --version 2>/dev/null",            "node"),
            "pm2":      ("pm2 --version 2>/dev/null",             "pm2"),
            "python":   ("python3 --version 2>/dev/null",         "Python"),
            "fail2ban": ("fail2ban-client --version 2>/dev/null | head -1", "Fail2Ban"),
            "ufw":      ("sudo ufw status 2>/dev/null | head -1", "Status"),
        }

        for task, (cmd, keyword) in checks.items():
            out, _ = self._exec(cmd)
            if out and keyword.lower() in out.lower():
                # Extract version string
                version = out.strip().split("\n")[0]
                installed[task] = version
            else:
                installed[task] = None

        # Check ssh_harden — is root login already disabled?
        out, _ = self._exec("sudo sshd -T 2>/dev/null | grep permitrootlogin")
        installed["ssh_harden"] = "already hardened" if "no" in out.lower() else None

        # Check auto_updates
        out, _ = self._exec("dpkg -l unattended-upgrades 2>/dev/null | grep '^ii'")
        installed["auto_updates"] = "installed" if out else None

        # Check infra containers — are they already running?
        infra_container_names = {
            "redis":    "infra_redis",
            "postgres": "infra_postgres",
            "mysql":    "infra_mysql",
            "mongodb":  "infra_mongodb",
        }
        for svc, container_name in infra_container_names.items():
            out, _ = self._exec(
                f"docker ps --filter name=^{container_name}$ --format '{{{{.Status}}}}' 2>/dev/null"
            )
            if out and "up" in out.lower():
                installed[svc] = f"running ({out.strip()})"
            else:
                installed[svc] = None

        return installed

    # ── Phase 1: gather via ONE LLM call ──────────────────────────────────────

    def _gather_context(self, query: str, prefill: Dict[str, Any] = None) -> SetupContext:
        """
        One LLM call to understand what the user wants.
        In API mode: prefill contains all answers upfront (from setup_params).
        In CLI mode: falls back to input() for missing info.
        Raises NeedsInputError if called from API and a required field is missing.
        """
        from app.services.conversation_service import NeedsInputError

        pf       = prefill or getattr(self, '_prefill', None) or {}
        api_mode = getattr(self, '_api_mode', False)

        # ── Early exit: plan was already confirmed by the user in a prior turn ──
        if pf.get("confirmed") and pf.get("tasks"):
            logger.info("[SETUP] Plan already confirmed — skipping LLM gather phase")
            ctx = SetupContext(
                tasks             = pf.get("tasks", []),
                infra_services    = pf.get("infra_services", []),
                extra_packages    = pf.get("extra_packages", []),
                extra_commands    = pf.get("extra_commands", []),
                firewall_ports    = pf.get("firewall_ports", []),
                open_ports        = pf.get("firewall_ports", []),
                suggestions       = pf.get("suggestions", []),
                server_purpose    = pf.get("server_purpose", ""),
                new_username      = pf.get("new_username", ""),
                # password may be keyed as 'new_user_password' (from ctx.__dict__)
                # or 'new_password' (from the need_password step prefill)
                new_user_password = pf.get("new_user_password", "") or pf.get("new_password", ""),
                your_public_key   = pf.get("your_public_key", ""),
            )
            self._save_context(ctx)
            return ctx

        # ── Resume mid-conversation: user added something at the confirm prompt ──
        resume_messages  = pf.pop("_resume_messages", None)
        extra_request    = pf.pop("_extra_request", None)

        # Tell the LLM which user is connected so it doesn't suggest bootstrap_user
        connected_as = getattr(self, '_connected_as', 'root')
        context_note = ""
        if connected_as and connected_as != "root":
            context_note = (
                f"\n\nIMPORTANT: The agent is already connected as user '{connected_as}' (not root). "
                f"Do NOT include 'bootstrap_user' in tasks. Do NOT set new_username. "
                f"The server already has a sudo user."
            )

        if resume_messages and extra_request:
            # Deserialise the saved LangChain messages and inject the user's addition
            logger.info(f"[SETUP] Resuming LLM conversation with extra request: {extra_request!r}")
            from langchain_core.messages import AIMessage
            messages = []
            for m in resume_messages:
                mtype = m.get("type") if isinstance(m, dict) else getattr(m, "type", None)
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                if mtype == "system":
                    messages.append(SystemMessage(content=content))
                elif mtype == "human":
                    messages.append(HumanMessage(content=content))
                elif mtype == "ai":
                    messages.append(AIMessage(content=content))
            messages.append(HumanMessage(
                content=f"Add these to the plan: {extra_request}. Return updated JSON."
            ))
        else:
            messages = [
                SystemMessage(content=_GATHER_SYSTEM + context_note),
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
                messages.append(HumanMessage(content="Return valid JSON only."))
                continue

            # LLM needs more info
            if data.get("missing"):
                question = data.get("question", "What would you like to set up?")
                if api_mode:
                    raise NeedsInputError(question, {
                        "step": "gather", "messages": messages,
                        "query": query, "prefill": pf,
                    })
                print(f"\n[?] {question}")
                answer = input("    Your answer: ").strip()
                messages.append(response)
                messages.append(HumanMessage(content=answer))
                continue            # Got the plan
            ctx = SetupContext(
                tasks          = data.get("tasks", []),
                infra_services = data.get("infra_services", []),
                extra_packages = data.get("extra_packages", []),
                extra_commands = data.get("extra_commands", []),
                firewall_ports = data.get("firewall_ports", []),
                open_ports     = data.get("firewall_ports", []),
                suggestions    = data.get("suggestions", []),
                server_purpose = data.get("server_purpose", ""),
                new_username   = data.get("new_username", "") or pf.get("new_username", ""),
                your_public_key= "",
            )

            # Guard: strip infra service names from tasks — they only belong in infra_services
            _infra_names = {"redis", "postgres", "mysql", "mongodb"}
            ctx.tasks = [t for t in ctx.tasks if t not in _infra_names]

            if ctx.infra_services and "docker" not in ctx.tasks:
                ctx.tasks.insert(0, "docker")

            # bootstrap_user — only when connected as root
            if "bootstrap_user" in ctx.tasks:
                connected_as = getattr(self, '_connected_as', 'root')
                if connected_as != "root":
                    # Not root — skip bootstrap_user entirely
                    logger.info(f"[SETUP] Skipping bootstrap_user — connected as '{connected_as}'")
                    ctx.tasks = [t for t in ctx.tasks if t != "bootstrap_user"]
                else:
                    # Connected as root — collect username, password, SSH key
                    if not ctx.new_username:
                        if api_mode:
                            raise NeedsInputError(
                                "What username should the new sudo user have? (e.g. deploy, nav, abhi)",
                                {"step": "need_username", "ctx_partial": {
                                    "tasks": ctx.tasks, "infra_services": ctx.infra_services,
                                    "server_purpose": ctx.server_purpose,
                                }, "prefill": pf}
                            )
                        ctx.new_username = input("\n[?] Username for new sudo user: ").strip() or "deploy"

                    if pf.get("new_password"):
                        ctx.new_user_password = pf["new_password"]
                    elif api_mode:
                        raise NeedsInputError(
                            f"Set a password for user '{ctx.new_username}' (min 8 characters):",
                            {"step": "need_password", "ctx_partial": {
                                "tasks": ctx.tasks, "new_username": ctx.new_username,
                                "server_purpose": ctx.server_purpose,
                            }, "prefill": pf}
                        )
                    else:
                        import getpass
                        while True:
                            pwd1 = getpass.getpass(f"\n    Password for '{ctx.new_username}': ")
                            pwd2 = getpass.getpass("    Confirm: ")
                            if pwd1 == pwd2 and len(pwd1) >= 8:
                                ctx.new_user_password = pwd1
                                break
                            print("    ✗ Passwords don't match or too short.")

                    # SSH public key is now REQUIRED, not optional, when
                    # bootstrap_user runs — because ssh_harden defaults to
                    # key-only auth. Skipping this here would silently set
                    # up a user who gets locked out the moment ssh_harden runs.
                    if pf.get("your_public_key"):
                        ctx.your_public_key = pf["your_public_key"]
                    elif api_mode:
                        raise NeedsInputError(
                            f"Paste the SSH public key for '{ctx.new_username}' "
                            f"(run: cat ~/.ssh/id_ed25519.pub on your machine). "
                            f"This is required — ssh_harden will enforce key-only login:",
                            {"step": "need_pubkey", "ctx_partial": {
                                "tasks": ctx.tasks, "new_username": ctx.new_username,
                                "server_purpose": ctx.server_purpose,
                            }, "prefill": pf}
                        )
                    else:
                        while True:
                            pubkey = input("\n    SSH public key (required): ").strip()
                            if pubkey.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256")) and len(pubkey.split()) >= 2:
                                ctx.your_public_key = pubkey
                                break
                            print("    ✗ Invalid key format.")

            # ── Show suggestions ───────────────────────────────────────────
            # Suggestions are disabled in API mode — user can just type what they want
            suggestions = []
            if not api_mode:
                suggestions = data.get("suggestions", [])
                suggestions = [s for s in suggestions if not any(
                    w in s.lower() for w in ("username", "sudo user", "bootstrap", "new user", "create user")
                )]

            if suggestions and not api_mode:
                print("\n" + "─" * 60)
                print("  💡 Suggestions — you might also need:")
                print("─" * 60)
                for i, s in enumerate(suggestions, 1):
                    print(f"  {i}. {s}")
                accept = input(
                    "\n[?] Accept all suggestions? (yes/no/partial)\n"
                    "    yes = add all  |  no = skip  |  partial = enter numbers (e.g. 1,3): "
                ).strip().lower()
                if accept == "yes":
                    messages.append(response)
                    messages.append(HumanMessage(content="Accept all suggestions and add them to the plan. Return updated JSON."))
                    continue
                elif accept not in ("no", ""):
                    chosen = [int(x.strip()) - 1 for x in accept.split(",") if x.strip().isdigit()]
                    chosen_text = ". ".join(suggestions[i] for i in chosen if i < len(suggestions))
                    if chosen_text:
                        messages.append(response)
                        messages.append(HumanMessage(content=f"Add these to the plan: {chosen_text}. Return updated JSON."))
                        continue

            # ── Show plan + ask for extras ─────────────────────────────────
            plan_summary = (
                f"Setup Plan — {ctx.server_purpose or 'Server Setup'}\n"
                f"Tasks: {', '.join(ctx.tasks) or 'none'}\n"
            )
            if ctx.new_username:
                plan_summary += f"New user: {ctx.new_username} (sudo)\n"
            if ctx.infra_services:
                plan_summary += f"Infra: {', '.join(ctx.infra_services)}\n"
            if ctx.extra_packages:
                plan_summary += f"Packages: {', '.join(ctx.extra_packages)}\n"

            if api_mode:
                raise NeedsInputError(
                    f"{plan_summary}\nAnything else to add? (e.g. 'also install postgresql') or type proceed to start.",
                    {"step": "confirm", "messages": messages,
                     "ctx_partial": ctx.__dict__, "prefill": pf}
                )

            # CLI mode — ask extras + confirm
            print("\n" + plan_summary)
            extra = input("[?] Anything else to add? Press Enter to skip: ").strip()
            if extra:
                messages.append(response)
                messages.append(HumanMessage(content=f"Add these to the plan: {extra}. Return updated JSON."))
                continue

            confirm = input("\n[?] Proceed with this setup? (yes/no): ").strip().lower()
            if not confirm.startswith("y"):
                print("Setup cancelled.")
                raise SystemExit(0)

            self._save_context(ctx)
            return ctx

    def _save_context(self, ctx: SetupContext):
        data = {
            "tasks":          ctx.tasks,
            "infra_services": ctx.infra_services,
            "extra_packages": ctx.extra_packages,
            "extra_commands": ctx.extra_commands,
            "firewall_ports": ctx.firewall_ports,
            "server_purpose": ctx.server_purpose,
            "new_username":   ctx.new_username,
            # NOTE: password is intentionally NOT saved — memory only
            # public key is safe to store (not a secret)
            "your_public_key": ctx.your_public_key,
        }
        with open("setup_context.json", "w") as f:
            json.dump(data, f, indent=2)
        logger.info("[SETUP] Context saved to setup_context.json")

    # ── Phase 2: execute steps directly ───────────────────────────────────────

    def _do_base(self):
        logger.info("[SETUP] Base packages")
        
        self._run(
            "while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 "
            "|| sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do "
            "echo 'Waiting for apt lock...'; sleep 3; done"
        )
        self._run("sudo apt-get update -y")
        self._run("sudo apt-get upgrade -y")
        self._run("sudo apt-get install -y curl git wget unzip build-essential software-properties-common")

    def _do_nginx(self):
        logger.info("[SETUP] Nginx")
        self.nginx.install()
        self.nginx.start()

    def _do_docker(self):
        logger.info("[SETUP] Docker")
        self.docker.install()
        self.docker.start_service()

    def _do_nodejs(self):
        logger.info("[SETUP] Node.js")
        # Install NodeSource LTS
        self._run("curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -")
        self._run("sudo apt-get install -y nodejs")

    def _do_pm2(self):
        logger.info("[SETUP] PM2")
        self.pm2.install()

    def _do_python(self):
        logger.info("[SETUP] Python")
        self._run("sudo apt-get install -y python3 python3-pip python3-venv")

    def _do_firewall(self, ctx: SetupContext):
        """
        Dynamic firewall setup:
          - Detects the ACTUAL ssh port in use (instead of assuming 22)
          - Only opens 80/443 if nginx is part of this setup
          - Only opens infra service ports if those services were requested
            (note: infra containers are bound to 127.0.0.1 only, so this is
             defense-in-depth, not strictly required for them to work)
          - Adds any explicitly requested firewall_ports from the user
        """
        logger.info("[SETUP] Firewall")

        out, _ = self._exec(
            "sudo grep -E '^Port ' /etc/ssh/sshd_config | awk '{print $2}'"
        )
        ssh_port = out.strip() or "22"

        self.executor.execute("sudo ufw --force reset")
        self.executor.execute("sudo ufw default deny incoming")
        self.executor.execute("sudo ufw default allow outgoing")

        ports_to_open = {ssh_port}

        if "nginx" in ctx.tasks:
            ports_to_open.update(["80", "443"])

        infra_ports = {"redis": "6379", "postgres": "5432", "mysql": "3306", "mongodb": "27017"}
        for svc in ctx.infra_services:
            if svc.lower() in infra_ports:
                ports_to_open.add(infra_ports[svc.lower()])

        ports_to_open.update(ctx.firewall_ports)

        for port in ports_to_open:
            self.firewall.allow_port(port)

        self.firewall.enable()

    def _do_fail2ban(self):
        """
        Installs fail2ban AND writes a jail config that whitelists this
        agent's own IP (self.agent_ignore_ips), so the agent's automated
        reconnects are never mistaken for a brute-force attempt and banned.
        """
        logger.info("[SETUP] Fail2ban")
        self.security.install_fail2ban()
        self.security.write_fail2ban_jail(ignore_ips=self.agent_ignore_ips)

    def _do_ssh_harden(self, ctx: SetupContext):
        """
        SSH hardening — key-only auth (production-safe default).

        SAFETY GUARD: before disabling password auth, verifies the target
        user actually has an SSH key installed. If not, refuses to proceed
        rather than risk locking the account out entirely. This is the
        check that would have prevented an earlier real lockout incident.
        """
        logger.info("[SETUP] SSH hardening — key-only auth")

        # Determine which user will need to reconnect after hardening:
        # if bootstrap_user ran this session, it's the new user; otherwise
        # it's whoever this agent is currently connected as.
        target_user = ctx.new_username if "bootstrap_user" in ctx.tasks and ctx.new_username else self._connected_as

        if not self.security.verify_key_installed(target_user):
            raise RuntimeError(
                f"Refusing to run ssh_harden: user '{target_user}' has no SSH key "
                f"in authorized_keys. Disabling password auth now would lock this "
                f"account out entirely. Install a key first (via bootstrap_user's "
                f"public_key param, or manually), then retry."
            )

        self.security.harden_ssh(max_auth_tries=3, disable_password_auth=True, require_two_factor=False)

    def _do_bootstrap_user(self, username: str, your_public_key: str = ""):
        """
        Must run BEFORE ssh_harden.
        Creates a new sudo user with a password (set during gather phase).
        User can SSH in with: ssh username@server  then enter their password.
        Optionally also installs an SSH public key if provided.
        """
        logger.info(f"[SETUP] Bootstrapping sudo user: {username}")

        password = getattr(self, '_bootstrap_password', '')
        if not password:
            raise ValueError(
                f"No password set for user '{username}'. "
                "Password must be collected before executing bootstrap_user."
            )

        self.security.bootstrap_sudo_user(
            username,
            password=password,
            public_key=your_public_key,
        )

        logger.info(
            f"[SETUP] User '{username}' created on {self.server_label}.\n"
            f"  Login with:  ssh {username}@<server-ip>  (then enter password)"
        )

        print(f"\n  ✓ User '{username}' created with sudo access.")
        print(f"    Login next time:  ssh {username}@<server-ip>")
        if your_public_key:
            print(f"    SSH key also installed (can login with key too).")

    def _do_auto_updates(self):
        logger.info("[SETUP] Automatic security updates")
        self.security.enable_unattended_upgrades()

    def _do_extra_packages(self, packages: List[str]):
        for pkg in packages:
            logger.info(f"[SETUP] Installing extra package: {pkg}")
            self.pkg.install(pkg)

    def _do_extra_commands(self, commands: List[str]):
        for cmd in commands:
            logger.info(f"[SETUP] Running custom command: {cmd}")
            self._run(cmd)

    def _do_infra(self, services: List[str]) -> Dict[str, Any]:
        """
        Runs requested infra services as Docker containers, bound to 127.0.0.1
        only (never exposed externally — nginx/app code talks to them over
        localhost). Credentials are generated randomly and saved via the key
        storage backend, never hardcoded or printed in plaintext to logs.
        """
        infra_definitions = {
            "redis": {
                "image": "redis:7-alpine",
                "container_name": "infra_redis",
                "internal_port": "6379",
            },
            "postgres": {
                "image": "postgres:16-alpine",
                "container_name": "infra_postgres",
                "internal_port": "5432",
                "env_template": lambda pw: {"POSTGRES_PASSWORD": pw, "POSTGRES_USER": "postgres"},
                "volume": "/var/lib/infra_postgres_data",
                "volume_target": "/var/lib/postgresql/data",
            },
            "mysql": {
                "image": "mysql:8",
                "container_name": "infra_mysql",
                "internal_port": "3306",
                "env_template": lambda pw: {"MYSQL_ROOT_PASSWORD": pw},
                "volume": "/var/lib/infra_mysql_data",
                "volume_target": "/var/lib/mysql",
            },
            "mongodb": {
                "image": "mongo:7",
                "container_name": "infra_mongodb",
                "internal_port": "27017",
                "env_template": lambda pw: {
                    "MONGO_INITDB_ROOT_USERNAME": "root",
                    "MONGO_INITDB_ROOT_PASSWORD": pw,
                },
                "volume": "/var/lib/infra_mongodb_data",
                "volume_target": "/data/db",
            },
        }

        connection_info: Dict[str, Any] = {}

        for svc in services:
            svc = svc.lower()
            if svc not in infra_definitions:
                logger.warning(f"[SETUP] Unknown infra service requested: {svc}")
                continue

            definition = infra_definitions[svc]
            port = definition["internal_port"]

            # Bind only to localhost — nginx/app code connects via 127.0.0.1, never external
            ports = {f"127.0.0.1:{port}": port}

            env = {}
            password = None
            if "env_template" in definition:
                password = self._generate_password()
                env = definition["env_template"](password)

            volumes = {}
            if "volume" in definition:
                volumes = {definition["volume"]: definition["volume_target"]}

            self.docker.run_container(
                name=definition["container_name"],
                image=definition["image"],
                ports=ports,
                env=env,
                volumes=volumes,
            )

            if password:
                ref = self.ssh_tool.key_storage.store_private_key(
                    f"infra_{svc}_{self.server_label}_password", password
                )
                connection_info[svc] = {"port": port, "credential_reference": ref}
                logger.warning(
                    f"[SETUP] {svc} credential generated and stored at: {ref} "
                    f"(not printed in plaintext)."
                )
            else:
                connection_info[svc] = {"port": port}

        return connection_info

    # ── Public entry point ─────────────────────────────────────────────────────

    def execute_task(self, query: str) -> str:
        logger.info("[SETUP] Phase 1 — gathering setup requirements via LLM...")
        prefill = getattr(self, '_prefill', None) or {}
        if prefill:
            self._api_mode = True

        # ── Check connected username BEFORE gather so bootstrap_user is skipped ──
        # Get username directly from executor config — avoids sanitizer scrubbing whoami output
        connected_as = getattr(self.executor, 'username', None) or 'unknown'
        self._connected_as = connected_as
        logger.info(f"[SETUP] Connected as: '{connected_as}'")

        # ── If connected as root, bootstrap_user is MANDATORY ─────────────────
        # Force it into the prefill/query so the LLM always includes it,
        # and the gather phase always asks for username/password/pubkey.
        if connected_as == "root":
            if "bootstrap_user" not in query.lower() and not prefill.get("new_username"):
                query = f"{query} (connected as root — must create a new sudo user first)"
            logger.info("[SETUP] Root connection detected — bootstrap_user will be enforced")

        ctx = self._gather_context(query, prefill=prefill)

        # ── Enforce bootstrap_user when connected as root ──────────────────────
        if connected_as == "root" and "bootstrap_user" not in ctx.tasks:
            logger.info("[SETUP] Injecting bootstrap_user — connected as root")
            ctx.tasks.insert(0, "bootstrap_user")

        # Store password temporarily in memory only — never written to disk
        self._bootstrap_password = ctx.new_user_password
        # ── Scan what's already on the server ─────────────────────────────────
        logger.info("[SETUP] Scanning server for existing installations...")
        print("\n" + "─" * 60)
        print("  Scanning server — checking what's already installed...")
        print("─" * 60)
        installed = self._scan_server()

        already_done = []
        tasks_to_run = []
        for task in ctx.tasks:
            if task in installed and installed[task]:
                already_done.append((task, installed[task]))
            else:
                tasks_to_run.append(task)

        # Check infra services against scan results
        infra_already_running = []
        infra_to_deploy = []
        for svc in ctx.infra_services:
            if installed.get(svc):
                infra_already_running.append((svc, installed[svc]))
            else:
                infra_to_deploy.append(svc)

        # Show scan results
        if already_done:
            print("\n  Already installed — will skip:")
            for task, version in already_done:
                print(f"    ✓ {task:<20} {version}")
        if infra_already_running:
            print("\n  Infra already running — will skip:")
            for svc, status in infra_already_running:
                print(f"    ✓ {svc:<20} {status}")
        if tasks_to_run:
            print("\n  Will install:")
            for task in tasks_to_run:
                print(f"    → {task}")
        if infra_to_deploy:
            print("\n  Will deploy infra:")
            for svc in infra_to_deploy:
                print(f"    → {svc}")
        if not tasks_to_run and not infra_to_deploy and not ctx.extra_packages:
            print("\n  ✓ Everything requested is already installed.")
        elif not tasks_to_run and not infra_to_deploy:
            pass  # extra_packages will be shown below

        if not tasks_to_run and not infra_to_deploy and not ctx.extra_packages:
            return "SETUP COMPLETE\n\nAll requested components are already installed. Nothing to do."

        # In API mode — skip the confirm prompt, just proceed
        if not getattr(self, '_api_mode', False):
            confirm = input("[?] Proceed with installation? (yes/no): ").strip().lower()
            if not confirm.startswith("y"):
                return "Setup cancelled."

        logger.info(f"[SETUP] Phase 2 — executing {len(tasks_to_run)} tasks...")
        results = []

        # Add already-done items to results as skipped
        for task, version in already_done:
            results.append(f"⏭ {task} (already installed: {version})")

        
        order_priority = {
            "bootstrap_user": 0,
            "base": 1,
            "docker": 2,
            "nginx": 2,
            "nodejs": 2,
            "python": 2,
            "pm2": 3,
            "fail2ban": 4,
            "auto_updates": 4,
            "firewall": 5,
            "ssh_harden": 6,  # last — riskiest step
        }
        ordered_tasks = sorted(tasks_to_run, key=lambda t: order_priority.get(t, 99))

        simple_task_map = {
            "base":         self._do_base,
            "nginx":        self._do_nginx,
            "docker":       self._do_docker,
            "nodejs":       self._do_nodejs,
            "pm2":          self._do_pm2,
            "python":       self._do_python,
            "fail2ban":     self._do_fail2ban,
            "auto_updates": self._do_auto_updates,
        }

        for task in ordered_tasks:
            try:
                if task == "firewall":
                    self._do_firewall(ctx)
                elif task == "bootstrap_user":
                    self._do_bootstrap_user(
                        ctx.new_username or "deploy",
                        your_public_key=ctx.your_public_key,
                    )
                elif task == "ssh_harden":
                    self._do_ssh_harden(ctx)
                elif task in simple_task_map:
                    simple_task_map[task]()
                else:
                    results.append(f"⚠ unknown task: {task}")
                    continue
                results.append(f"✓ {task}")
            except Exception as e:
                logger.error(f"[SETUP] {task} failed: {e}")
                results.append(f"✗ {task}: {e}")
                # If a risky step fails, stop the chain to avoid cascading damage
                if task in ("ssh_harden", "bootstrap_user"):
                    logger.error(f"[SETUP] Stopping further execution after critical failure in '{task}'")
                    break

        # Extra packages
        if ctx.extra_packages:
            try:
                self._do_extra_packages(ctx.extra_packages)
                results.append(f"✓ extra packages: {', '.join(ctx.extra_packages)}")
            except Exception as e:
                results.append(f"✗ extra packages: {e}")

        # Infrastructure services (Redis, Postgres etc. as shared Docker containers)
        # Add already-running infra to results as skipped
        for svc, status in infra_already_running:
            results.append(f"⏭ {svc} (already running: {status})")

        if infra_to_deploy:
            try:
                connection_info = self._do_infra(infra_to_deploy)
                results.append(f"✓ infra services: {', '.join(infra_to_deploy)}")
                print("\n  Infrastructure connection info (localhost-only):")
                for svc, info in connection_info.items():
                    print(f"    {svc}: 127.0.0.1:{info['port']}")
                    if "credential_reference" in info:
                        print(f"      Credential stored securely at: {info['credential_reference']}")
            except Exception as e:
                results.append(f"✗ infra services: {e}")

        # Extra commands
        if ctx.extra_commands:
            try:
                self._do_extra_commands(ctx.extra_commands)
                results.append(f"✓ custom commands: {len(ctx.extra_commands)} ran")
            except Exception as e:
                results.append(f"✗ custom commands: {e}")

        summary = "\n".join(results)
        failed  = [r for r in results if r.startswith("✗")]

        # Clear password from memory — it was only needed during bootstrap_user
        self._bootstrap_password = ""

        if failed:
            self.alerter.warning(
                title="Server setup completed with errors",
                server=self.server_label,
                details="\n".join(failed),
            )
        else:
            self.alerter.info(
                title="Server setup completed successfully",
                server=self.server_label,
                details=f"Tasks: {', '.join(ctx.tasks)}",
            )

        return f"SETUP COMPLETE\n\n{summary}"

    # ── Convenience: add a teammate's public key to an existing user ───────────

    def add_team_member(self, public_key: str, home_dir: str = "/home/deploy") -> str:
        """
        Adds a teammate's PUBLIC key (never their private key) to the
        authorized_keys of an existing user on this server. Call this using
        an executor that is already authorized (e.g. your own key_filename),
        not the original root/password credentials (which stop working after
        ssh_harden runs).
        """
        return self.ssh_tool.add_authorized_key(public_key, home_dir=home_dir)