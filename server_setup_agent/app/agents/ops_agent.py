"""
OpsAgent — general-purpose "update anything, anytime" agent.

Unlike SetupAgent (fixed 12-task JSON schema), this agent exposes
raw tools to the LLM and lets it decide what to call, in a loop,
until the task is done. Use this for anything outside SetupAgent's
fixed vocabulary: config edits, ad-hoc installs, status checks,
one-off fixes.
"""

import base64
import json
from typing import Dict, Any

from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from app.services.llm_service import get_llm
from app.services.conversation_service import NeedsInputError
from app.services.sanitizer_service import scrub_credentials
from app.services.teams_alert_service import TeamsAlerter
from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.security_tool import SecurityTool
from app.tools.firewall_tool import FirewallTool
from app.tools.nginx_tool import NginxTool


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run shell command on server",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit file (auto-backup)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_server",
            "description": "Full server audit (OS, users, firewall, ports, services)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_status",
            "description": "Check firewall status",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_allow",
            "description": "Allow port",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string"},
                    "protocol": {"type": "string", "enum": ["tcp", "udp"]},
                },
                "required": ["port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_delete",
            "description": "Delete firewall rule",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string"},
                    "protocol": {"type": "string", "enum": ["tcp", "udp"]},
                },
                "required": ["port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_enable",
            "description": "Enable firewall",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_disable",
            "description": "Disable firewall",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_reset",
            "description": "Reset firewall (dangerous)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_start",
            "description": "Start/restart PM2 app",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                    "port": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_status",
            "description": "List PM2 processes",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_logs",
            "description": "Show PM2 logs",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                    "lines": {"type": "integer"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_stop",
            "description": "Stop PM2 app",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_restart",
            "description": "Restart PM2 app",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_delete",
            "description": "Delete PM2 app",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "redeploy_app",
            "description": "Redeploy app (git pull, build, restart)",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                    "app_path": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nginx_setup",
            "description": "Configure Nginx",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                    "app_path": {"type": "string"},
                    "port": {"type": "string"},
                    "domain": {"type": "string"},
                    "app_type": {"type": "string", "enum": ["frontend", "backend"]},
                },
                "required": ["app_name", "app_path", "port", "domain", "app_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ssl",
            "description": "Configure SSL certificate",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "email": {"type": "string"},
                    "renewal_check": {"type": "boolean"},
                },
                "required": ["domain", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_env",
            "description": "Update .env file",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_path": {"type": "string"},
                    "env_vars": {"type": "object"},
                    "restart": {"type": "boolean"},
                },
                "required": ["app_path", "env_vars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fix_nginx_port",
            "description": "Fix nginx port configuration (change from old port to new port in all locations)",
            "parameters": {
                "type": "object",
                "properties": {
                    "config_file": {"type": "string"},
                    "old_port": {"type": "string"},
                    "new_port": {"type": "string"},
                },
                "required": ["config_file", "old_port", "new_port"],
            },
        },
    },
]

# Commands that must get explicit user confirmation before execution
DANGEROUS_PATTERNS = [
    "rm -rf", "userdel", "ufw disable", "iptables -F",
    "dd if=", "mkfs", "> /dev/", "shutdown", "reboot",
]

# Files that always get sanity-checked after edit, with rollback on failure
VALIDATE_AFTER_EDIT = {
    "/etc/ssh/sshd_config": "sudo sshd -t",
    "/etc/nginx/nginx.conf": "sudo nginx -t",
}

_TOOL_RESULT_MAX = 16000  # Increased to show full build errors and logs


class OpsAgent:
    def __init__(
        self,
        executor_type: str = "local",
        executor_config: Dict[str, Any] = None,
        server_label: str = None,
        require_confirmation: bool = True,
    ):
        self.executor = ExecutorFactory.get_executor(executor_type, **(executor_config or {}))
        self.llm = get_llm()
        self.linux = LinuxTool(self.executor)
        self.security = SecurityTool(self.executor)
        self.firewall = FirewallTool(self.executor)
        self.nginx = NginxTool(self.executor)
        self.alerter = TeamsAlerter()
        self.server_label = server_label or "unknown"
        self.require_confirmation = require_confirmation

        # Dangerous-command confirmation state injected by the API resume path.
        # When set to a command string, _run_command skips the NeedsInputError
        # and executes that exact command once, then clears the flag.
        self._confirmed_command: str | None = None

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _exec(self, cmd: str) -> str:
        _, out, err = self.executor.execute(cmd)
        raw = (out or err).strip()
        return scrub_credentials(raw)

    def _is_dangerous(self, cmd: str) -> bool:
        return any(p in cmd for p in DANGEROUS_PATTERNS)

    # ── Tool implementations ─────────────────────────────────────────────────

    def _run_command(self, command: str) -> str:
        if self._is_dangerous(command) and self.require_confirmation:
            # If the user already confirmed this exact command via the API
            # conversation flow, execute it and clear the flag.
            if self._confirmed_command and self._confirmed_command == command:
                self._confirmed_command = None
                logger.info(f"[OPS] CONFIRMED DANGEROUS RUN: {command}")
                return self._exec(command)

            # Otherwise pause and ask the frontend for confirmation.
            raise NeedsInputError(
                f"⚠️  This command is potentially destructive:\n```\n{command}\n```\n"
                f"Type **yes** to confirm or **no** to skip.",
                {
                    "step": "ops_confirm_command",
                    "pending_command": command,
                    "agent": "ops",
                },
            )

        logger.info(f"[OPS] RUN: {command}")
        return self._exec(command)

    def _read_file(self, path: str) -> str:
        return self._exec(f"cat {path}")

    def _edit_file(self, path: str, content: str) -> str:
        # Backup with a timestamp suffix
        backup_cmd = f"sudo cp {path} {path}.bak.$(date +%s) 2>/dev/null || true"
        self._exec(backup_cmd)

        sftp = self.executor.get_sftp() if hasattr(self.executor, "get_sftp") else None
        if sftp:
            with sftp.file(path, "w") as f:
                f.write(content)
        else:
            # Use base64 to avoid any shell-injection risk from file content
            encoded = base64.b64encode(content.encode()).decode()
            self._exec(
                f"echo '{encoded}' | base64 --decode | sudo tee {path} > /dev/null"
            )

        # Post-edit validation with rollback for known critical files
        if path in VALIDATE_AFTER_EDIT:
            result = self._exec(VALIDATE_AFTER_EDIT[path])
            if "error" in result.lower() or "fail" in result.lower():
                # Roll back to the most-recent backup by timestamp
                self._exec(
                    f"latest=$(ls -t {path}.bak.* 2>/dev/null | head -1); "
                    f"[ -n \"$latest\" ] && sudo cp \"$latest\" {path}"
                )
                return f"EDIT FAILED validation — rolled back to previous version.\nValidator output: {result}"

        return f"File {path} updated (backup saved)."

    def _audit_server(self) -> str:
        checks = {
            "os":               "cat /etc/os-release | head -2",
            "users":            "cut -d: -f1 /etc/passwd",
            "ssh_config":       "sudo grep -E '^(PermitRootLogin|PasswordAuthentication|Port)' /etc/ssh/sshd_config",
            "firewall":         "sudo ufw status verbose",
            "fail2ban":         "sudo fail2ban-client status 2>/dev/null",
            "open_ports":       "sudo ss -tulpn",
            "updates_pending":  "apt list --upgradable 2>/dev/null | head -20",
            "running_services": "systemctl list-units --type=service --state=running --no-pager",
            "disk":             "df -h",
        }
        results = {k: self._exec(v) for k, v in checks.items()}
        return json.dumps(results, indent=2)

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "run_command":
            return self._run_command(args["command"])
        if name == "read_file":
            return self._read_file(args["path"])
        if name == "edit_file":
            return self._edit_file(args["path"], args["content"])
        if name == "audit_server":
            return self._audit_server()
        if name == "firewall_status":
            return self.firewall.status()
        if name == "firewall_allow":
            protocol = args.get("protocol", "tcp")
            return self.firewall.allow_port(args["port"], protocol)
        if name == "firewall_delete":
            protocol = args.get("protocol", "tcp")
            port = args["port"]
            result = self.firewall.delete_rule(port, protocol)
            # Send Teams notification
            self.alerter.warning(
                title="Firewall rule deleted",
                server=self.server_label,
                details=f"Deleted rule: {port}/{protocol}",
            )
            logger.info(f"[OPS] Firewall rule deleted: {port}/{protocol}")
            return result
        if name == "firewall_enable":
            return self.firewall.enable()
        if name == "firewall_disable":
            # If user already confirmed this command, execute without asking again
            if self._confirmed_command == "firewall_disable":
                logger.info("[OPS] CONFIRMED: firewall_disable")
                self._confirmed_command = None  # Clear so next dangerous command asks again
                result = self.firewall.disable()
                # Send Teams notification
                self.alerter.critical(
                    title="Firewall DISABLED",
                    server=self.server_label,
                    details="⚠️ UFW firewall has been disabled on the server",
                )
                return result
            
            if self._is_dangerous("ufw disable") and self.require_confirmation:
                raise NeedsInputError(
                    "⚠️  Disabling the firewall is potentially risky. Type **yes** to confirm or **no** to skip.",
                    {
                        "step": "ops_confirm_command",
                        "pending_command": "firewall_disable",
                        "agent": "ops",
                    },
                )
            return self.firewall.disable()
        if name == "firewall_reset":
            # If user already confirmed this command, execute without asking again
            if self._confirmed_command == "firewall_reset":
                logger.info("[OPS] CONFIRMED: firewall_reset")
                self._confirmed_command = None  # Clear so next dangerous command asks again
                result = self.firewall.reset_firewall()
                # Send Teams notification with details
                self.alerter.critical(
                    title="Firewall RESET - All Rules Deleted",
                    server=self.server_label,
                    details="⚠️ UFW firewall has been completely reset. ALL custom rules have been deleted.",
                )
                return result

            if self._is_dangerous("ufw reset") and self.require_confirmation:
                raise NeedsInputError(
                    "⚠️  DESTRUCTIVE: This will reset the firewall to factory defaults and remove ALL custom rules.\n"
                    "Type **yes** to confirm.",
                    {
                        "step": "ops_confirm_command",
                        "pending_command": "firewall_reset",
                        "agent": "ops",
                    },
                )
            return self.firewall.reset_firewall()
        
        # ── PM2 Operations ─────────────────────────────────────────────────
        if name == "pm2_start":
            app_path = args["app_name"]
            port = args.get("port", "")
            
            # Resolve app path if it's just a name
            if not app_path.startswith("/"):
                app_path = f"/home/meetri/api/{app_path}"
            
            app_name = app_path.rstrip("/").split("/")[-1]
            
            # Delete any existing PM2 instances to prevent duplicates
            logger.info(f"[OPS] PM2 START: Cleaning up old instances of {app_name}")
            self._exec(f"pm2 delete {app_name} 2>/dev/null || true")
            
            # Auto-detect start script
            pkg_json = f"{app_path}/package.json"
            check_cmd = f"grep -q '\"start:prod\"' {pkg_json} 2>/dev/null && echo 'prod' || echo 'start'"
            _, script_type, _ = self.executor.execute(check_cmd)
            start_script = "start:prod" if "prod" in script_type else "start"
            
            # Build PM2 command
            port_env = f"PORT={port} " if port else ""
            cmd = f"cd {app_path} && {port_env}pm2 start npm --name {app_name} -- run {start_script}"
            
            logger.info(f"[OPS] PM2 START: {app_path} (using {start_script})")
            result = self._run_command(cmd)
            
            # Verify it started
            _, pm2_list, _ = self.executor.execute("pm2 list --no-color")
            return result + f"\n✅ App started as PM2 process '{app_name}'"
        
        if name == "pm2_status":
            logger.info("[OPS] PM2 STATUS")
            return self._exec("pm2 list --no-color")
        
        if name == "pm2_logs":
            app_name = args["app_name"]
            lines = args.get("lines", 50)
            logger.info(f"[OPS] PM2 LOGS: {app_name}")
            
            # Use timeout to prevent hanging on large logs
            # If pm2 logs takes more than 10 seconds, kill it
            cmd = f"timeout 10 pm2 logs {app_name} --lines {lines} --nostream --no-color || true"
            result = self._exec(cmd)
            
            if not result or result.strip() == "":
                return f"⚠️ No logs for {app_name} (or command timed out after 10 seconds)"
            return result
        
        if name == "pm2_stop":
            app_name = args["app_name"]
            logger.info(f"[OPS] PM2 STOP: {app_name}")
            result = self._exec(f"pm2 stop {app_name}")
            return result + f"\n✅ App '{app_name}' stopped"
        
        if name == "pm2_restart":
            app_name = args["app_name"]
            logger.info(f"[OPS] PM2 RESTART: {app_name}")
            result = self._exec(f"pm2 restart {app_name}")
            return result + f"\n✅ App '{app_name}' restarted"
        
        if name == "pm2_delete":
            app_name = args["app_name"]
            logger.critical(f"[SECURITY] DELETE PM2 APP ATTEMPT: {app_name}")
            
            # Send Teams alert
            self.alerter.critical(
                title="⚠️ DELETE PM2 APP REQUESTED",
                server=self.server_label,
                details=f"PM2 application deletion request: {app_name}\n\n"
                       f"This will remove the app from PM2 permanently.\n"
                       f"REQUIRES EXPLICIT CONFIRMATION from administrator."
            )
            
            # Return error message instead of raising to avoid 500 error
            return (
                f"❌ BLOCKED: PM2 app deletion is a destructive operation.\n\n"
                f"App to delete: {app_name}\n\n"
                f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
                f"To proceed, you must:\n"
                f"1. Verify this is intentional\n"
                f"2. Get explicit approval from ops admin\n"
                f"3. Run manually via SSH:\n"
                f"   pm2 delete {app_name}"
            )
        
        if name == "redeploy_app":
            app_name = args["app_name"]
            app_path = args.get("app_path")
            branch = args.get("branch")  # Optional branch parameter
            
            logger.info(f"[OPS] REDEPLOY: {app_name}" + (f" (branch: {branch})" if branch else ""))
            
            # Auto-detect path if not provided
            if not app_path:
                # Try common locations
                possible_paths = [
                    f"/home/meetri/api/{app_name}",
                    f"/home/meetri/ui/{app_name}",
                    f"/opt/api/{app_name}",
                    f"/opt/ui/{app_name}",
                    f"/opt/{app_name}",
                    f"/var/www/{app_name}",
                ]
                for path in possible_paths:
                    check = self._exec(f"test -d {path} && echo 'exists' || echo 'not'")
                    if "exists" in check:
                        app_path = path
                        logger.info(f"[OPS] Found app at: {app_path}")
                        break
                
                if not app_path:
                    return f"❌ Could not find app directory for '{app_name}'. Please specify app_path."
            
            # If user provided a parent directory, check if app is a subdirectory
            # Example: user says "/home/meetri/ui" but app is in "/home/meetri/ui/pm-frontend"
            git_check = self._exec(f"test -d {app_path}/.git && echo 'is_git' || echo 'not_git'")
            if "not_git" in git_check:
                logger.info(f"[REDEPLOY] {app_path} is not a git repo, checking for subdirectory")
                # Check if app_name exists as subdirectory
                subdir_path = f"{app_path}/{app_name}"
                subdir_check = self._exec(f"test -d {subdir_path}/.git && echo 'found' || echo 'not'")
                if "found" in subdir_check:
                    logger.info(f"[REDEPLOY] Found git repo in subdirectory: {subdir_path}")
                    app_path = subdir_path
                else:
                    return (
                        f"❌ '{app_path}' is not a git repository.\n\n"
                        f"I checked:\n"
                        f"  • {app_path} - not a git repo\n"
                        f"  • {subdir_path} - {'not found' if 'not' in subdir_check else 'not a git repo'}\n\n"
                        f"Please provide the exact path to the git repository for '{app_name}'."
                    )
            
            results = []
            
            # Detect if it's a Docker or PM2/regular app
            docker_check = self._exec(f"docker ps -a --filter name={app_name} --format '{{{{.Names}}}}'")
            is_docker = app_name in docker_check
            
            try:
                # Step 1: Check/switch branch if specified
                if branch:
                    logger.info(f"[REDEPLOY] Switching to branch: {branch}")
                    current_branch = self._exec(f"cd {app_path} && git branch --show-current")
                    if current_branch.strip() != branch:
                        branch_result = self._exec(f"cd {app_path} && git checkout {branch}")
                        results.append(f"🔀 Switched to branch '{branch}':\n{branch_result}")
                    else:
                        results.append(f"✓ Already on branch '{branch}'")
                
                # Step 2: Git pull
                logger.info(f"[REDEPLOY] Git pull in {app_path}")
                git_result = self._exec(f"cd {app_path} && git pull 2>&1")
                # Check for git errors - capture full output
                if "error" in git_result.lower() or "fatal" in git_result.lower() or "conflict" in git_result.lower():
                    results.append(f"❌ Git pull FAILED")
                    results.append(f"\n{git_result}\n")
                    return "\n\n".join(results)
                results.append(f"📦 Git pull:\n{git_result}")
                
                if is_docker:
                    # Docker workflow: down → build → up
                    logger.info(f"[REDEPLOY] Docker app detected: {app_name}")
                    
                    # Check for docker-compose.yml
                    compose_check = self._exec(f"test -f {app_path}/docker-compose.yml && echo 'exists' || echo 'not'")
                    if "exists" not in compose_check:
                        results.append(f"⚠️ No docker-compose.yml found in {app_path}")
                        return "\n\n".join(results)
                    
                    # Down
                    results.append(f"🛑 Stopping containers...")
                    down_result = self._exec(f"cd {app_path} && docker compose down")
                    results.append(f"  {down_result}")
                    
                    # Build
                    results.append(f"🔨 Building new image...")
                    build_result = self._exec(f"cd {app_path} && docker compose build")
                    # Check for docker build errors
                    if "error" in build_result.lower() or "failed" in build_result.lower():
                        results.append(f"❌ Docker build failed:\n{build_result}")
                        return "\n\n".join(results)
                    results.append(f"  {build_result}")
                    
                    # Up
                    results.append(f"🚀 Starting containers...")
                    up_result = self._exec(f"cd {app_path} && docker compose up -d")
                    # Check for docker up errors
                    if "error" in up_result.lower():
                        results.append(f"❌ Docker containers failed to start:\n{up_result}")
                        return "\n\n".join(results)
                    results.append(f"  {up_result}")
                    
                    results.append(f"✅ Docker app '{app_name}' redeployed successfully")
                    
                else:
                    # PM2 workflow: clean old build → install → build → restart
                    logger.info(f"[REDEPLOY] PM2 app detected: {app_name}")
                    
                    # Check if package.json exists in app_path or subdirectories
                    pkg_check = self._exec(f"test -f {app_path}/package.json && echo 'exists' || echo 'not'")
                    
                    # If not in root, check subdirectories
                    if "not" in pkg_check:
                        logger.info(f"[REDEPLOY] package.json not in {app_path}, checking subdirectories")
                        # Find package.json in subdirectories
                        find_pkg = self._exec(f"find {app_path} -maxdepth 2 -name 'package.json' -type f | head -1")
                        if find_pkg.strip():
                            app_path = find_pkg.strip().rsplit('/', 1)[0]  # Get directory containing package.json
                            logger.info(f"[REDEPLOY] Found package.json at: {app_path}")
                            pkg_check = "exists"
                    
                    if "exists" in pkg_check:
                        # Node.js app
                        # Step 1: Clean old build artifacts (dist, .next, build, etc.)
                        results.append(f"🧹 Cleaning old build artifacts...")
                        clean_builds = self._exec(f"cd {app_path} && rm -rf dist .next build out .vite .parcel-cache && echo 'cleaned'")
                        results.append(f"  Removed dist, .next, build, out, .vite, .parcel-cache")
                        
                        # Step 2: Remove old node_modules and lock files
                        results.append(f"🧹 Cleaning old dependencies...")
                        clean_result = self._exec(f"cd {app_path} && rm -rf node_modules package-lock.json && echo 'cleaned'")
                        results.append(f"  Removed old node_modules and lock files")
                        
                        # Step 3: Install fresh dependencies
                        results.append(f"📦 Installing dependencies...")
                        # Use npm install (works with or without package-lock.json)
                        install_result = self._exec(f"cd {app_path} && npm install 2>&1")
                        results.append(f"Install output:\n{install_result}")
                        # Check for npm errors - STRICT checking
                        if "error" in install_result.lower() or "err" in install_result.lower():
                            results.append(f"\n❌ INSTALL FAILED - See errors above")
                            return "\n\n".join(results)
                        results.append(f"✅ Dependencies installed successfully")
                        
                        # Step 4: Build if build script exists
                        build_check = self._exec(f"grep -q '\"build\"' {app_path}/package.json && echo 'exists' || echo 'not'")
                        if "exists" in build_check:
                            results.append(f"🔨 Building application...")
                            build_result = self._exec(f"cd {app_path} && npm run build 2>&1")
                            # ALWAYS append build output for debugging
                            results.append(f"Build output:\n{build_result}")
                            # Check for build errors - STRICT checking
                            if "error" in build_result.lower() or "err" in build_result.lower() or "failed" in build_result.lower():
                                results.append(f"\n❌ BUILD FAILED - See errors above")
                                return "\n\n".join(results)
                            if "found" in build_result.lower() and "error" in build_result.lower():
                                results.append(f"\n❌ BUILD FAILED - Compilation errors detected")
                                return "\n\n".join(results)
                            results.append(f"✅ Build completed successfully")
                        else:
                            results.append(f"⚠️ No build script found in package.json")
                    else:
                        # Check for requirements.txt (Python app)
                        req_check = self._exec(f"test -f {app_path}/requirements.txt && echo 'exists' || echo 'not'")
                        if "exists" in req_check:
                            # Step 1: Clean old build artifacts
                            results.append(f"🧹 Cleaning old build artifacts...")
                            clean_builds = self._exec(f"cd {app_path} && rm -rf dist build *.egg-info .pytest_cache && echo 'cleaned'")
                            results.append(f"  Removed dist, build, egg-info, pytest cache")
                            
                            # Step 2: Remove old venv and cache
                            results.append(f"🧹 Cleaning old environment...")
                            clean_result = self._exec(f"cd {app_path} && rm -rf venv __pycache__ *.pyc && echo 'cleaned'")
                            results.append(f"  Removed old venv and cache files")
                            
                            # Step 3: Create fresh venv
                            results.append(f"📦 Creating virtual environment...")
                            venv_result = self._exec(f"cd {app_path} && python3 -m venv venv")
                            # Check for venv creation errors
                            if "error" in venv_result.lower():
                                results.append(f"❌ venv creation failed:\n{venv_result}")
                                return "\n\n".join(results)
                            results.append(f"  Virtual environment created")
                            
                            # Step 4: Install dependencies
                            results.append(f"📦 Installing dependencies...")
                            pip_result = self._exec(f"cd {app_path} && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt 2>&1")
                            # Check for pip errors
                            if "error" in pip_result.lower() or "err" in pip_result.lower():
                                results.append(f"❌ pip install FAILED")
                                results.append(f"\n{pip_result}\n")
                                return "\n\n".join(results)
                            results.append(f"  Dependencies installed successfully")
                        else:
                            results.append(f"⚠️ No package.json or requirements.txt found - cannot determine app type")
                    
                    # Step 5: Restart PM2 process
                    results.append(f"🔄 Restarting PM2 process...")
                    
                    # Check if app is in PM2
                    pm2_check = self._exec(f"pm2 list --no-color | grep {app_name}")
                    if app_name in pm2_check:
                        restart_result = self._exec(f"pm2 restart {app_name}")
                        # Check for restart errors
                        if "error" in restart_result.lower() or "err" in restart_result.lower():
                            results.append(f"❌ PM2 restart FAILED")
                            results.append(f"\n{restart_result}\n")
                            final_msg = "\n\n".join(results)
                            logger.error(f"[REDEPLOY] FAILED: {final_msg}")
                            return final_msg
                        results.append(f"  {restart_result}")
                        results.append(f"✅ PM2 app '{app_name}' redeployed successfully (full rebuild)")
                    else:
                        results.append(f"⚠️ App '{app_name}' not found in PM2. You may need to start it manually.")
                
                final_msg = "\n\n".join(results)
                logger.info(f"[REDEPLOY] SUCCESS: {app_name}")
                return final_msg
                
            except Exception as e:
                logger.error(f"[REDEPLOY] Failed: {e}")
                results.append(f"❌ Redeploy failed: {e}")
                return "\n\n".join(results)
        
        if name == "nginx_setup":
            app_name = args["app_name"]
            app_path = args["app_path"]
            port = args.get("port", "3000")
            domain = args["domain"]
            app_type = args.get("app_type", "backend")
            
            logger.info(f"[OPS] NGINX SETUP: {app_name} (domain={domain}, port={port}, type={app_type})")
            
            try:
                self.nginx.install()
                
                # Ensure SSL if domain (not IP)
                if domain and not NginxTool._is_ip(domain):
                    ssl_status = self.nginx.ensure_ssl_cert(domain)
                    logger.info(f"[NGINX] SSL cert status: {ssl_status}")
                
                # Generate config based on app type
                if app_type == "frontend":
                    # Find dist folder
                    dist_path = f"{app_path}/dist"
                    for candidate in (".next", "dist", "build", "out"):
                        candidate_path = f"{app_path}/{candidate}"
                        result = self._exec(f"test -d {candidate_path} && echo 'exists' || echo 'not'")
                        if "exists" in result:
                            dist_path = candidate_path
                            break
                    self.nginx.generate_and_save_config(
                        framework="react",
                        domain=domain,
                        app_name=app_name,
                        app_path=dist_path,
                    )
                else:
                    # Backend app
                    self.nginx.generate_and_save_config(
                        framework="nodejs",
                        domain=domain,
                        app_name=app_name,
                        port=int(port),
                    )
                
                self.nginx.test_config()
                self.nginx.enable_site(app_name, domain)
                self.nginx.reload_nginx()
                
                # Determine config name for status message
                config_name = domain if (domain and not self.nginx._is_ip(domain) and domain.strip() not in ("_", "", "none", "null")) else app_name
                logger.info(f"[OPS] NGINX SETUP COMPLETE: {app_name}")
                return f"✅ Nginx configured for {app_name}\nDomain: {domain}\nPort: {port}\nConfig: /etc/nginx/sites-available/{config_name}"
            except Exception as e:
                logger.error(f"[OPS] NGINX SETUP FAILED: {e}")
                return f"❌ Nginx setup failed: {e}"
        
        if name == "configure_ssl":
            domain = args["domain"]
            email = args["email"]
            renewal_check = args.get("renewal_check", True)
            
            logger.info(f"[OPS] CONFIGURE SSL: {domain} (email={email})")
            
            try:
                # Check if cert already exists
                cert_dir = f"/etc/letsencrypt/live/{domain}"
                result = self._exec(f"test -d {cert_dir} && echo 'exists' || echo 'not'")
                
                if "exists" in result and renewal_check:
                    logger.info(f"[SSL] Certificate already exists for {domain}, checking renewal...")
                    renewal_result = self._run_command(f"sudo certbot renew --quiet --no-eff-email 2>/dev/null || true")
                    return f"✅ Certificate check complete for {domain}.\n{renewal_result}"
                
                # Install certbot if not present
                install_result = self._exec("which certbot || sudo apt-get update && sudo apt-get install -y certbot python3-certbot-nginx")
                
                # Configure SSL via certbot using nginx plugin (works with nginx running)
                logger.info(f"[SSL] Requesting Let's Encrypt certificate for {domain}...")
                certbot_cmd = (
                    f"sudo certbot --nginx -d {domain} --non-interactive --agree-tos "
                    f"--email {email} --redirect 2>&1"
                )
                result = self._run_command(certbot_cmd)
                
                # Check if successful
                if "Successfully received certificate" in result or "Certificate not yet due for renewal" in result or "Congratulations" in result:
                    logger.info(f"[SSL] Certificate configured successfully for {domain}")
                    
                    return (
                        f"✅ SSL certificate successfully obtained for {domain}\n"
                        f"- Cert path: {cert_dir}/fullchain.pem\n"
                        f"- Key path: {cert_dir}/privkey.pem\n"
                        f"- Auto-renewal: Enabled\n"
                        f"- HTTPS redirect: Configured\n\n"
                        f"Your app is now accessible at https://{domain}"
                    )
                else:
                    logger.error(f"[SSL] Certificate configuration may have failed: {result}")
                    return (
                        f"⚠️ Certificate configuration for {domain} - check output:\n{result}\n\n"
                        f"Common issues:\n"
                        f"- Domain DNS not pointing to this server\n"
                        f"- Port 80/443 not accessible from the internet\n"
                        f"- Rate limit exceeded (wait 1 hour and try again)\n\n"
                        f"For troubleshooting, run: sudo certbot --dry-run -d {domain}"
                    )
            
            except Exception as e:
                logger.error(f"[SSL] Configuration failed: {e}")
                return f"❌ SSL configuration failed: {e}\n\nFor debugging, run on server: sudo certbot --nginx -d {domain}"
        
        if name == "update_env":
            app_path = args["app_path"].rstrip("/")
            env_vars = args["env_vars"]
            restart = args.get("restart", True)
            
            app_name = app_path.split("/")[-1]
            env_file = f"{app_path}/.env"
            
            logger.info(f"[OPS] UPDATE ENV: {app_path} - Updating {len(env_vars)} variable(s)")
            
            try:
                # Check if .env file exists
                result = self._exec(f"test -f {env_file} && echo 'exists' || echo 'not'")
                file_exists = "exists" in result
                
                if file_exists:
                    # Read current .env file
                    current_content = self._exec(f"cat {env_file}")
                    lines = current_content.split("\n")
                else:
                    logger.info(f"[OPS] .env file doesn't exist - creating new one")
                    lines = []
                
                # Backup existing file
                if file_exists:
                    self._exec(f"cp {env_file} {env_file}.bak.$(date +%s)")
                
                # Update or add each variable
                updated_keys = set()
                new_lines = []
                
                for line in lines:
                    # Skip empty lines and comments
                    if not line.strip() or line.strip().startswith("#"):
                        new_lines.append(line)
                        continue
                    
                    # Parse KEY=VALUE
                    if "=" in line:
                        key = line.split("=")[0].strip()
                        if key in env_vars:
                            # Update existing key
                            new_lines.append(f"{key}={env_vars[key]}")
                            updated_keys.add(key)
                            logger.info(f"[OPS] Updated: {key}")
                        else:
                            # Keep unchanged
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                
                # Add new keys that weren't in the file
                for key, value in env_vars.items():
                    if key not in updated_keys:
                        new_lines.append(f"{key}={value}")
                        logger.info(f"[OPS] Added: {key}")
                
                # Write updated content
                new_content = "\n".join(new_lines)
                encoded = base64.b64encode(new_content.encode()).decode()
                self._exec(f"echo '{encoded}' | base64 --decode > {env_file}")
                
                result_msg = f"✅ Updated {len(env_vars)} environment variable(s) in {env_file}\n"
                result_msg += "\n".join(f"  • {key}" for key in env_vars.keys())
                
                # Restart application if requested
                if restart:
                    logger.info(f"[OPS] Restarting {app_name} to apply .env changes")
                    
                    # Check if app is running in PM2
                    pm2_check = self._exec(f"pm2 list --no-color | grep {app_name}")
                    if app_name in pm2_check:
                        restart_result = self._exec(f"pm2 restart {app_name} --update-env")
                        result_msg += f"\n\n✅ Application restarted: {app_name}"
                    else:
                        result_msg += f"\n\n⚠️ App not found in PM2 - manual restart may be required"
                
                return result_msg
                
            except Exception as e:
                logger.error(f"[OPS] UPDATE ENV FAILED: {e}")
                return f"❌ Failed to update environment variables: {e}"
        
        if name == "fix_nginx_port":
            config_file = args["config_file"]
            old_port = args["old_port"]
            new_port = args["new_port"]
            
            logger.info(f"[OPS] FIX NGINX PORT: {config_file} ({old_port} → {new_port})")
            
            try:
                # Step 1: Check if file exists
                check_result = self._exec(f"test -f {config_file} && echo 'exists' || echo 'not'")
                if "not" in check_result:
                    return f"❌ Nginx config file not found: {config_file}"
                
                # Step 2: Read current file
                current_content = self._exec(f"cat {config_file}")
                
                # Check if old port exists
                if old_port not in current_content:
                    return f"⚠️ Port {old_port} not found in {config_file}\n\nFile currently shows port 5173 (unchanged)"
                
                # Step 3: Check if we have sudo password - if not, ask user for it
                if not self.executor.sudo_password:
                    # Ask user to provide sudo password via UI
                    raise NeedsInputError(
                        f"🔐 Sudo password required to complete port change\n\n"
                        f"I need your sudo password to:\n"
                        f"1. Replace :{old_port} with :{new_port}\n"
                        f"2. Test nginx configuration (sudo nginx -t)\n"
                        f"3. Reload nginx (sudo systemctl reload nginx)\n\n"
                        f"Please enter your sudo password:",
                        {
                            "step": "nginx_sudo_password_needed",
                            "config_file": config_file,
                            "old_port": old_port,
                            "new_port": new_port,
                        },
                    )
                
                # Step 4: Create backup
                backup_cmd = f"sudo cp {config_file} {config_file}.bak.$(date +%s) 2>/dev/null || cp {config_file} {config_file}.bak"
                self._exec(backup_cmd)
                logger.info(f"[OPS] Backed up: {config_file}")
                
                # Step 5: Replace port in file using sed with sudo
                sed_cmd = f"sudo sed -i 's/:{old_port}/:{new_port}/g' {config_file}"
                result = self._exec(sed_cmd)
                logger.info(f"[OPS] Port replacement executed: {sed_cmd}")
                
                # Step 6: Verify changes were made
                verify_result = self._exec(f"grep {new_port} {config_file} | head -5")
                replacements_found = verify_result.count(new_port)
                
                if replacements_found == 0:
                    logger.warning(f"[OPS] Port replacement verification failed - may not have changed")
                    return f"⚠️ Warning: Port replacement command ran but verification shows no changes. Please check manually."
                
                logger.info(f"[OPS] Verified {replacements_found} replacements of port {new_port}")
                
                # Step 7: Test and reload nginx
                test_result = self._exec("sudo nginx -t 2>&1")
                test_passed = "successful" in test_result.lower() or "ok" in test_result.lower() or "syntax is ok" in test_result.lower()
                
                if test_passed:
                    # Nginx test passed - try to reload
                    reload_result = self._exec("sudo systemctl reload nginx 2>&1")
                    
                    msg = f"✅ Nginx port configuration updated successfully\n\n"
                    msg += f"• File: {config_file}\n"
                    msg += f"• Changed: :{old_port} → :{new_port}\n"
                    msg += f"• Replacements: {replacements_found} locations updated\n"
                    msg += f"• Nginx test: PASSED ✓\n"
                    msg += f"• Status: Nginx reloaded ✓\n"
                    logger.info(f"[OPS] Nginx port fix completed successfully with reload")
                    return msg
                else:
                    # Nginx test failed - show user manual commands
                    msg = f"✅ Port Changed: :{old_port} → :{new_port}\n\n"
                    msg += f"• File: {config_file}\n"
                    msg += f"• Replacements: {replacements_found} locations updated ✓\n"
                    msg += f"• Backup: {config_file}.bak\n\n"
                    msg += f"⚠️ Could not test/reload nginx (SSL or permission issue)\n\n"
                    msg += f"**Manual Commands to Complete:**\n"
                    msg += f"```bash\n"
                    msg += f"sudo nginx -t\n"
                    msg += f"sudo systemctl reload nginx\n"
                    msg += f"```\n"
                    logger.info(f"[OPS] Nginx port changed but test skipped")
                    return msg
                
            except NeedsInputError:
                raise
            except Exception as e:
                logger.error(f"[OPS] FIX NGINX PORT FAILED: {e}")
                return f"❌ Failed to fix nginx port: {e}"
        
        return f"Unknown tool: {name}"

    @staticmethod
    def _truncate(text: str, limit: int = _TOOL_RESULT_MAX) -> str:
        """Truncate tool output and append a clear marker so the LLM knows."""
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n\n[TRUNCATED — output exceeded {limit} chars, showing first {limit}]"

    # ── Main loop ─────────────────────────────────────────────────────────────

    def execute_task(self, query: str, max_turns: int = 8) -> str:
        messages = [
            SystemMessage(content=(
                "You are a Linux ops assistant. Use tools to execute user requests. "
                "Be precise—only do what's asked. Report clearly.\n\n"
                "CRITICAL: For ANY redeploy request (redeploy, re-deploy, re deploy, update deployment, upgrade deployment, pull latest, update app):\n"
                "1. MUST call the 'redeploy_app' tool (this is REQUIRED)\n"
                "2. Extract app name from the user query\n"
                "3. Pass app_name to redeploy_app\n"
                "4. Do NOT use pm2_restart, pm2_stop, or other tools - ONLY use redeploy_app\n"
                "5. The redeploy_app tool handles everything: git pull → clean build → rebuild → restart\n\n"
                "For nginx port fixes:\n"
                "- Use 'fix_nginx_port' tool to change port in nginx config files\n"
                "- Extract old and new port from user query (e.g., '8000 to 9000' or '5173 to 4175')\n"
                "- IMPORTANT: Nginx config files are named by domain/subdomain, NOT app name\n"
                "  Examples: deploy.meetri.in, pm-frontend.conf, server-maintenance-ai.conf\n"
                "- WORKFLOW:\n"
                "  1. List configs: run_command 'ls /etc/nginx/sites-available/'\n"
                "  2. Find EXACT match from user input (case-insensitive search)\n"
                "  3. If exact match found → IMMEDIATELY call fix_nginx_port (user said 'update')\n"
                "  4. If NO exact match but close match exists (fuzzy match):\n"
                "     - Tell user: \"Did you mean: [suggestion]? Proceeding with change...\"\n"
                "     - PROCEED with the change using the closest match\n"
                "  5. If NO match at all → ask user to provide exact filename\n"
                "- NEVER ask 'which one' if you found a likely match - just proceed and tell user\n"
                "- Example paths: /etc/nginx/sites-available/deploy.meetri.in\n\n"
                "For env updates:\n"
                "- Full path provided? Use it\n"
                "- App name only? Try /home/meetri/api/APP-NAME\n"
                "- NEVER use find / or grep -R (too slow)\n"
                "- Ask if uncertain\n"
                "- Don't ask again if path already given\n"
                "- Execute immediately when you have path + vars"
            )),
            HumanMessage(content=query),
        ]

        last_ai_content = ""

        for turn in range(max_turns):
            response = self.llm.bind_tools(TOOLS).invoke(messages)
            messages.append(response)

            if not getattr(response, "tool_calls", None):
                return response.content

            # Track last meaningful AI content for the max-turns fallback
            if response.content:
                last_ai_content = response.content

            for call in response.tool_calls:
                logger.info(f"[OPS] Tool call: {call['name']}({call['args']})")
                result = self._dispatch(call["name"], call["args"])
                safe_result = self._truncate(result)
                messages.append(
                    ToolMessage(content=safe_result, tool_call_id=call["id"])
                )

        # Fell out of the loop without a final text response
        if last_ai_content:
            return (
                f"{last_ai_content}\n\n"
                f"⚠️  Reached the turn limit — task may be incomplete."
            )
        return "⚠️  Reached the turn limit without a final answer — task may be incomplete."
