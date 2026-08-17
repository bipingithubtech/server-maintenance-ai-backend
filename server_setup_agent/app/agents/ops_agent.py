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
            
            if self.require_confirmation:
                raise NeedsInputError(
                    "⚠️  Resetting firewall will delete ALL custom rules. Type **yes** to confirm or **no** to skip.",
                    {
                        "step": "ops_confirm_command",
                        "pending_command": "firewall_reset",
                        "agent": "ops",
                    },
                )
            return self.firewall.reset_firewall()
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
                "changes beyond what was requested. Report clearly what you did."
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
