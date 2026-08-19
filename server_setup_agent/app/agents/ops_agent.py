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
            "description": "Run any shell command on the server. Use for status checks, installs, service restarts, anything not covered by other tools.",
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
            "description": "Read a file's contents from the server.",
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
            "description": "Overwrite a file on the server with new content. Automatically backs up the original first.",
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
            "description": "Run a full server state audit — OS, users, ssh config, firewall, fail2ban, open ports, installed packages, pending updates, running services.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_status",
            "description": "Check current UFW firewall status and list all open ports.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_allow",
            "description": "Allow traffic on a specific port. Example: allow port 8080, or allow port 3000/tcp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "Port number (e.g., '8080', '3000', '3306')"},
                    "protocol": {"type": "string", "enum": ["tcp", "udp"], "default": "tcp"},
                },
                "required": ["port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_delete",
            "description": "Remove/delete a firewall rule for a specific port. Example: delete rule for port 8080.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "Port number (e.g., '8080', '3000')"},
                    "protocol": {"type": "string", "enum": ["tcp", "udp"], "default": "tcp"},
                },
                "required": ["port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_enable",
            "description": "Enable the UFW firewall.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_disable",
            "description": "Disable the UFW firewall (use with caution).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "firewall_reset",
            "description": "Completely reset the firewall to factory defaults - removes ALL custom rules (very dangerous, requires confirmation).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_start",
            "description": "Start or restart an app with PM2. Automatically detects start:prod or start script from package.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name/path (e.g., 'luna-backend' or '/home/meetri/api/luna-backend')"},
                    "port": {"type": "string", "description": "Port number (optional, e.g., '3000')"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_status",
            "description": "Show all PM2 processes and their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pm2_logs",
            "description": "Show logs for a specific PM2 app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name (e.g., 'luna-backend')"},
                    "lines": {"type": "integer", "description": "Number of lines to show (default: 50)"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nginx_setup",
            "description": "Configure Nginx for an app anytime (even after deployment). Can be used to add or reconfigure Nginx for apps that skipped it during deployment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name (e.g., 'luna-backend')"},
                    "app_path": {"type": "string", "description": "Full app path (e.g., '/home/meetri/api/luna-backend')"},
                    "port": {"type": "string", "description": "Internal port the app listens on (e.g., '3000')"},
                    "domain": {"type": "string", "description": "Domain or IP for Nginx (e.g., 'pm.meetri.in' or '192.168.1.1')"},
                    "app_type": {"type": "string", "enum": ["frontend", "backend"], "description": "App type: frontend (static files) or backend (process)"},
                },
                "required": ["app_name", "app_path", "port", "domain", "app_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_ssl",
            "description": "Configure SSL/HTTPS for a domain using Let's Encrypt. Run this after deployment to set up SSL certificates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain to configure (e.g., 'deploy.meetri.in')"},
                    "email": {"type": "string", "description": "Email for Let's Encrypt notifications (e.g., 'admin@meetri.in')"},
                    "renewal_check": {"type": "boolean", "description": "Check existing certificate status before renewal (default: true)"},
                },
                "required": ["domain", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_env",
            "description": "Update or add environment variables to an application's .env file. Automatically backs up the file before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_path": {"type": "string", "description": "Path to application directory (e.g., '/home/meetri/api/luna-backend')"},
                    "env_vars": {
                        "type": "object",
                        "description": "Key-value pairs of environment variables to update (e.g., {'PORT': '3000', 'API_KEY': 'new_key'})",
                        "additionalProperties": {"type": "string"}
                    },
                    "restart": {"type": "boolean", "description": "Restart the application after updating .env (default: true)"},
                },
                "required": ["app_path", "env_vars"],
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

_TOOL_RESULT_MAX = 4000


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
                "You are a Linux server operations assistant. You have tools to run "
                "commands, read/edit files, and audit server state. Use them to "
                "accomplish exactly what the user asks. Be surgical — don't make "
                "changes beyond what was requested. Report clearly what you did.\n\n"
                "IMPORTANT: When asked to update environment variables:\n"
                "1. If the user provides a full path (e.g., /home/user/api/app-name), use it directly\n"
                "2. If the user only provides an app name, assume it's in /home/meetri/api/APP-NAME\n"
                "3. NEVER use 'find /' or 'grep -R /home' to search for apps - they are too slow\n"
                "4. If uncertain about the path, ask the user instead of searching\n"
                "5. If the user already provided the path in the conversation, USE IT - don't ask again\n"
                "6. Execute the update immediately if you have both the path and the variables\n\n"
                "Common app locations:\n"
                "- /home/meetri/api/APP-NAME\n"
                "- /opt/APP-NAME\n"
                "- /var/www/APP-NAME"
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
