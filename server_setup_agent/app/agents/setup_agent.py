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
  - ssh_harden     : disable root login, disable password auth, set MaxAuthTries
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
    tasks:           List[str]
    infra_services:  List[str]
    extra_packages:  List[str]
    extra_commands:  List[str]
    firewall_ports:  List[str]
    open_ports:      List[str]
    suggestions:     List[str] = field(default_factory=list)
    server_purpose:  str = ""
    new_username:    str = ""   # used by bootstrap_user task


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
  ssh_harden       - disable root SSH login, disable password auth, set MaxAuthTries (key-only login)
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
                   - "ssh_harden not included — recommended to disable root login and password auth"
                   - "auto_updates not included — recommended so the server keeps patching itself"
                   - "if connecting as root on a fresh server, consider bootstrap_user to create a sudo user before hardening SSH"
                   Only suggest things that are genuinely missing and useful. Keep suggestions concise.

Rules:
- If user mentions redis/postgres/mysql/mongodb: put in infra_services (NOT extra_packages)
- infra_services always require docker task to be included
- If user mentions "root", "fresh server", or "create a user": include bootstrap_user and ask for new_username if not given
- If user says "full setup": include all tasks + common infra (redis, postgres)
- NEVER guess firewall_ports unless user explicitly mentions them
- bootstrap_user should run BEFORE ssh_harden in practice (handled by execution order, not by you)
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
    ):
        if executor_config is None:
            executor_config = {}

        self.executor     = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.llm          = get_llm()
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")

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

    # ── Phase 1: gather via ONE LLM call ──────────────────────────────────────

    def _gather_context(self, query: str) -> SetupContext:
        """One LLM call to understand what the user wants, then ask for extras."""

        messages = [
            SystemMessage(content=_GATHER_SYSTEM),
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
                print(f"\n[?] {data.get('question', 'What would you like to set up?')}")
                answer = input("    Your answer: ").strip()
                messages.append(response)
                messages.append(HumanMessage(content=answer))
                continue

            # Got the plan — show it to the user
            ctx = SetupContext(
                tasks          = data.get("tasks", []),
                infra_services = data.get("infra_services", []),
                extra_packages = data.get("extra_packages", []),
                extra_commands = data.get("extra_commands", []),
                firewall_ports = data.get("firewall_ports", []),
                open_ports     = data.get("firewall_ports", []),
                suggestions    = data.get("suggestions", []),
                server_purpose = data.get("server_purpose", ""),
                new_username   = data.get("new_username", ""),
            )

            # If infra services need docker, ensure docker is in tasks
            if ctx.infra_services and "docker" not in ctx.tasks:
                ctx.tasks.insert(0, "docker")

            # bootstrap_user requested but no username given — ask
            if "bootstrap_user" in ctx.tasks and not ctx.new_username:
                username = input("\n[?] What username should the new sudo user have? ").strip()
                ctx.new_username = username or "deploy"

            # ── Show suggestions (things user may have missed) ─────────────
            suggestions = data.get("suggestions", [])
            if suggestions:
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
                    # Re-ask LLM to merge suggestions into the plan
                    messages.append(response)
                    messages.append(HumanMessage(
                        content="Accept all suggestions and add them to the plan. Return updated JSON."
                    ))
                    continue
                elif accept not in ("no", ""):
                    # Partial — user entered numbers
                    chosen = [int(x.strip()) - 1 for x in accept.split(",") if x.strip().isdigit()]
                    chosen_text = ". ".join(suggestions[i] for i in chosen if i < len(suggestions))
                    if chosen_text:
                        messages.append(response)
                        messages.append(HumanMessage(
                            content=f"Add these to the plan: {chosen_text}. Return updated JSON."
                        ))
                        continue

            # ── Show the plan ──────────────────────────────────────────────
            print("\n" + "─" * 60)
            print(f"  Setup Plan — {ctx.server_purpose or 'Server Setup'}")
            print("─" * 60)
            print(f"  Tasks:     {', '.join(ctx.tasks) or 'none'}")
            if ctx.new_username:
                print(f"  New user:  {ctx.new_username} (sudo)")
            if ctx.infra_services:
                print(f"  Infra:     {', '.join(ctx.infra_services)} (shared Docker containers)")
            if ctx.extra_packages:
                print(f"  Packages:  {', '.join(ctx.extra_packages)}")
            if ctx.firewall_ports:
                print(f"  Ports:     {', '.join(ctx.firewall_ports)}")
            if ctx.extra_commands:
                print(f"  Commands: {len(ctx.extra_commands)} custom command(s)")

            # ── Ask for extras ─────────────────────────────────────────────
            print()
            extra = input(
                "[?] Anything else to add? (e.g. 'also install postgresql and open port 5432')\n"
                "    Press Enter to skip: "
            ).strip()

            if extra:
                # One more LLM call to parse the extras
                messages.append(response)
                messages.append(HumanMessage(
                    content=f"Add these to the plan: {extra}. Return updated JSON."
                ))
                continue

            # ── Confirm ────────────────────────────────────────────────────
            confirm = input("\n[?] Proceed with this setup? (yes/no): ").strip().lower()
            if not confirm.startswith("y"):
                print("Setup cancelled.")
                raise SystemExit(0)

            # Save context
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
        }
        with open("setup_context.json", "w") as f:
            json.dump(data, f, indent=2)
        logger.info("[SETUP] Context saved to setup_context.json")

    # ── Phase 2: execute steps directly ───────────────────────────────────────

    def _do_base(self):
        logger.info("[SETUP] Base packages")
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
        logger.info("[SETUP] Fail2ban")
        self.security.install_fail2ban()

    def _do_ssh_harden(self):
        logger.info("[SETUP] SSH hardening")
        self.security.harden_ssh(max_auth_tries=3, disable_password_auth=True)

    def _do_bootstrap_user(self, username: str):
        """
        Must run BEFORE ssh_harden. Creates a new sudo user, generates an SSH
        keypair LOCALLY (private key never touches the remote server), and
        installs only the public key on the remote authorized_keys.
        """
        logger.info(f"[SETUP] Bootstrapping sudo user: {username}")

        key_info = self.ssh_tool.generate_local_keypair(
            key_name=f"{username}_{self.server_label}",
        )

        self.security.bootstrap_sudo_user(username, key_info["public_key"])

        logger.warning(
            f"[SETUP] User '{username}' created on {self.server_label}.\n"
            f"  Private key reference: {key_info['private_key_reference']}\n"
            f"  This is the ONLY way to log in as '{username}' once root/password login is disabled."
        )

        return key_info

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
        ctx = self._gather_context(query)

        logger.info(f"[SETUP] Phase 2 — executing {len(ctx.tasks)} tasks...")
        results = []

        # Enforce safe ordering: bootstrap_user must run before ssh_harden,
        # and ssh_harden/firewall should run near the end (after everything
        # else is installed) so a config mistake doesn't block remaining steps.
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
        ordered_tasks = sorted(ctx.tasks, key=lambda t: order_priority.get(t, 99))

        simple_task_map = {
            "base":         self._do_base,
            "nginx":        self._do_nginx,
            "docker":       self._do_docker,
            "nodejs":       self._do_nodejs,
            "pm2":          self._do_pm2,
            "python":       self._do_python,
            "fail2ban":     self._do_fail2ban,
            "ssh_harden":   self._do_ssh_harden,
            "auto_updates": self._do_auto_updates,
        }

        for task in ordered_tasks:
            try:
                if task == "firewall":
                    self._do_firewall(ctx)
                elif task == "bootstrap_user":
                    self._do_bootstrap_user(ctx.new_username or "deploy")
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
        if ctx.infra_services:
            try:
                connection_info = self._do_infra(ctx.infra_services)
                results.append(f"✓ infra services: {', '.join(ctx.infra_services)}")
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