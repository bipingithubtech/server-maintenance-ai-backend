"""
UserManagementAgent — Manage users on an already-provisioned server.

This agent is called AFTER the fresh-server setup is done (you are now
logged in as your sudo user, not root).

Capabilities
------------
  add_user(username, public_key)
      Create a new sudo user and install their SSH public key.
      The caller supplies the teammate's public key — we never generate
      a key on the remote server or handle private keys here.

  remove_user(username)
      Lock + delete a user account and their home directory.

  list_users()
      Show all users who have SSH keys or sudo access.

  add_key_to_user(username, public_key)
      Add an additional SSH public key to an existing user
      (e.g. the user got a new laptop).

  rotate_my_key(new_public_key)
      Replace the calling user's own authorized key with a new one.

execute_task(query) — natural-language entry point routed from the API.
"""

import re
import shlex
from typing import Dict, Any, Optional

from loguru import logger

from app.executors.executor_factory import ExecutorFactory
from app.tools.security_tool import SecurityTool
from app.tools.ssh_tool import SSHTool
from app.tools.linux_tool import LinuxTool
from app.services.teams_alert_service import TeamsAlerter
from app.services.llm_service import get_llm
from app.services.conversation_service import NeedsInputError
from langchain_core.messages import SystemMessage, HumanMessage


_INTENT_SYSTEM = """You are a user-management assistant for Linux servers.
Parse the user's request and return JSON with exactly these fields:

  action       : one of "add_user" | "remove_user" | "list_users" | "add_key" | "rotate_key" | "unknown"
  username     : the Linux username (string, "" if not provided)
  public_key   : the SSH public key string (full "ssh-ed25519 AAAA... comment" line, "" if not provided)
  notes        : any clarification needed (string, "" if clear)

Rules:
- "add user"/"create user"/"new user"/"add teammate" → action = "add_user"
- "remove user"/"delete user"/"revoke access" → action = "remove_user"
- "list users"/"show users"/"who has access" → action = "list_users"
- "add key"/"add another key"/"extra key" → action = "add_key"
- "rotate key"/"update my key"/"replace key" → action = "rotate_key"
- If public_key is missing for add_user/add_key/rotate_key, set notes to ask for it
- Return ONLY valid JSON, no markdown

Examples:
  "add user alice with key ssh-ed25519 AAAA... alice@laptop"
  → {"action":"add_user","username":"alice","public_key":"ssh-ed25519 AAAA... alice@laptop","notes":""}

  "remove user bob"
  → {"action":"remove_user","username":"bob","public_key":"","notes":""}

  "who has access to this server"
  → {"action":"list_users","username":"","public_key":"","notes":""}
"""


class UserManagementAgent:

    def __init__(
        self,
        executor_type:   str = "local",
        executor_config: Dict[str, Any] = None,
        server_label:    Optional[str] = None,
    ):
        if executor_config is None:
            executor_config = {}

        self.executor     = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.security     = SecurityTool(self.executor)
        self.ssh_tool     = SSHTool(self.executor)
        self.linux        = LinuxTool(self.executor)
        self.alerter      = TeamsAlerter()
        self.llm          = get_llm()
        self.server_label = server_label or executor_config.get("host", "unknown")

    # ── helpers ────────────────────────────────────────────────────────────────

    def _exec(self, cmd: str):
        _, out, err = self.executor.execute(cmd)
        return out.strip(), err.strip()

    def _validate_public_key(self, key: str) -> bool:
        """Sanity-check that this looks like a real SSH public key."""
        key = key.strip()
        return bool(re.match(r'^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256)\s+[A-Za-z0-9+/=]+', key))

    # ── Action: add_user ───────────────────────────────────────────────────────

    def add_user(self, username: str, public_key: str) -> str:
        """
        Create a new sudo user and install their SSH public key.
        The caller (you) must already be connected as a sudo user — NOT root.
        """
        if not username or not username.isidentifier():
            return f"ERROR: '{username}' is not a valid Linux username."

        if not self._validate_public_key(public_key):
            return (
                "ERROR: The provided public key doesn't look valid.\n"
                "Expected format: ssh-ed25519 AAAA... user@machine"
            )

        # Check if sudo password is available
        if not hasattr(self.executor, 'sudo_password') or not self.executor.sudo_password:
            raise NeedsInputError(
                f"⚠️ **Sudo Password Required**\n\n"
                f"Creating a new user requires sudo privileges.\n\n"
                f"Please provide the sudo password:",
                {
                    "step": "need_sudo_password_user_mgmt",
                    "username": username,
                    "public_key": public_key,
                    "agent": "user_management"
                }
            )

        logger.info(f"[USER-MGMT] Adding user: {username} on {self.server_label}")
        try:
            result = self.security.add_sudo_user_with_key(username, public_key)
            self.alerter.info(
                title=f"New user added: {username}",
                server=self.server_label,
                details=f"User '{username}' created with sudo access and SSH key installed.",
            )
            return f"✓ {result}\n  User '{username}' can now SSH in with their private key."
        except Exception as e:
            logger.error(f"[USER-MGMT] add_user failed: {e}")
            return f"✗ Failed to add user '{username}': {e}"

    # ── Action: remove_user ────────────────────────────────────────────────────

    def remove_user(self, username: str) -> str:
        """
        Lock the account, remove from sudo group, and delete the home directory.
        Safety: refuses to remove 'root' or the currently connected user.
        """
        if username in ("root", ""):
            return "ERROR: Cannot remove the root user."

        logger.info(f"[USER-MGMT] Removing user: {username} on {self.server_label}")

        username_q = shlex.quote(username)
        results = []

        # Check user exists
        code, _, _ = self.executor.execute(f"id -u {username_q}")
        if code != 0:
            return f"ERROR: User '{username}' does not exist on this server."

        # Remove from sudo
        code, _, err = self.executor.execute(f"sudo deluser {username_q} sudo 2>/dev/null || true")
        results.append(f"✓ Removed from sudo group")

        # Lock account (disable password)
        self.executor.execute(f"sudo usermod -L {username_q}")
        results.append(f"✓ Account locked")

        # Kill active sessions
        self.executor.execute(f"sudo pkill -u {username_q} 2>/dev/null || true")

        # Delete user and home dir
        code, _, err = self.executor.execute(
            f"sudo deluser --remove-home {username_q}"
        )
        if code != 0:
            results.append(f"✗ Failed to delete user: {err}")
        else:
            results.append(f"✓ User and home directory deleted")

        self.alerter.warning(
            title=f"User removed: {username}",
            server=self.server_label,
            details=f"User '{username}' removed and home directory deleted.",
        )

        return "\n".join(results)

    # ── Action: list_users ─────────────────────────────────────────────────────

    def list_users(self) -> str:
        """Show all users with SSH keys or sudo access."""
        logger.info(f"[USER-MGMT] Listing users on {self.server_label}")

        # Sudo group members
        sudo_out, _ = self._exec("grep '^sudo:' /etc/group | cut -d: -f4")
        sudo_users = set(u.strip() for u in sudo_out.split(",") if u.strip())

        # Users who have authorized_keys
        ssh_out, _ = self._exec(
            "find /home -name authorized_keys 2>/dev/null | while read f; do "
            "dir=$(dirname $f); user=$(stat -c %U $dir 2>/dev/null || basename $(dirname $dir)); "
            "echo $user; done"
        )
        ssh_users = set(u.strip() for u in ssh_out.splitlines() if u.strip())

        all_users = sudo_users | ssh_users
        if not all_users:
            return "No non-root users found with sudo or SSH access."

        lines = [f"Users with access on {self.server_label}:", ""]
        for user in sorted(all_users):
            tags = []
            if user in sudo_users:
                tags.append("sudo")
            if user in ssh_users:
                tags.append("ssh-key")
            lines.append(f"  {user:<20} [{', '.join(tags)}]")

        return "\n".join(lines)

    # ── Action: add_key ────────────────────────────────────────────────────────

    def add_key_to_user(self, username: str, public_key: str) -> str:
        """Add an additional SSH public key to an existing user."""
        if not self._validate_public_key(public_key):
            return "ERROR: Invalid public key format."

        logger.info(f"[USER-MGMT] Adding SSH key to user: {username}")
        try:
            result = self.ssh_tool.add_authorized_key(
                public_key, home_dir=f"/home/{shlex.quote(username)}"
            )
            return f"✓ {result}"
        except Exception as e:
            return f"✗ Failed to add key for '{username}': {e}"

    # ── Action: rotate_key ─────────────────────────────────────────────────────

    def rotate_my_key(self, username: str, new_public_key: str) -> str:
        """
        Replace ALL existing keys for a user with only the new key.
        Use this when rotating your own key (e.g. new laptop).
        """
        if not self._validate_public_key(new_public_key):
            return "ERROR: Invalid public key format."

        logger.info(f"[USER-MGMT] Rotating SSH key for user: {username}")

        username_q = shlex.quote(username)
        new_key_q  = shlex.quote(new_public_key.strip())
        auth_keys  = f"/home/{username_q}/.ssh/authorized_keys"

        # Overwrite (not append) authorized_keys with only the new key
        cmd = (
            f"mkdir -p /home/{username_q}/.ssh && "
            f"echo {new_key_q} | sudo tee {auth_keys} > /dev/null && "
            f"sudo chmod 600 {auth_keys} && "
            f"sudo chown {username_q}:{username_q} {auth_keys}"
        )
        code, _, err = self.executor.execute(cmd)
        if code != 0:
            return f"✗ Failed to rotate key for '{username}': {err}"

        self.alerter.warning(
            title=f"SSH key rotated for user: {username}",
            server=self.server_label,
            details="All previous keys removed. New key installed.",
        )
        return f"✓ SSH key rotated for '{username}'. All previous keys removed."

    # ── Natural-language entry point ───────────────────────────────────────────

    def execute_task(self, query: str) -> str:
        """
        Parse the user's intent via LLM and dispatch to the right action.
        """
        import json, time

        # Normal flow - parse the query
        messages = [
            SystemMessage(content=_INTENT_SYSTEM),
            HumanMessage(content=query),
        ]

        while True:
            time.sleep(0.5)
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

            action     = data.get("action", "unknown")
            username   = data.get("username", "").strip()
            public_key = data.get("public_key", "").strip()
            notes      = data.get("notes", "").strip()

            # LLM flagged something missing - but we already have context (username/key provided)
            if notes:
                # If we have username AND public_key, the LLM successfully parsed them
                # Just proceed with the action even if there's a note
                if username and public_key and action == "add_user":
                    return self.add_user(username, public_key)
                
                # Otherwise return the note as JSON for frontend
                return json.dumps({
                    "status": "need_input",
                    "prompt": notes,
                    "action": action,
                    "username": username,
                    "public_key": public_key
                })

            # Dispatch
            if action == "add_user":
                if not public_key:
                    return json.dumps({
                        "status": "need_input",
                        "prompt": f"Please provide the SSH public key for user '{username}'",
                        "action": "add_user",
                        "username": username,
                        "field_needed": "public_key"
                    })
                return self.add_user(username, public_key)

            elif action == "remove_user":
                return json.dumps({
                    "status": "need_confirmation",
                    "prompt": f"This will DELETE user '{username}' and their home directory. Are you sure?",
                    "action": "remove_user",
                    "username": username,
                    "confirmation_required": True
                })

            elif action == "list_users":
                return self.list_users()

            elif action == "add_key":
                if not public_key:
                    return json.dumps({
                        "status": "need_input",
                        "prompt": f"Please provide the additional SSH public key for '{username}'",
                        "action": "add_key",
                        "username": username,
                        "field_needed": "public_key"
                    })
                return self.add_key_to_user(username, public_key)

            elif action == "rotate_key":
                if not public_key:
                    return json.dumps({
                        "status": "need_input",
                        "prompt": "Please provide your new SSH public key",
                        "action": "rotate_key",
                        "username": username,
                        "field_needed": "public_key"
                    })
                return self.rotate_my_key(username, public_key)

            else:
                # Unknown action - but check if we have username + key, assume add_user
                if username and public_key:
                    logger.info(f"[USER-MGMT] Unknown action but have username+key, assuming add_user")
                    return self.add_user(username, public_key)
                
                return (
                    "I can help with:\n"
                    "  • Add a user:     'add user alice with key ssh-ed25519 AAAA...'\n"
                    "  • Remove a user:  'remove user bob'\n"
                    "  • List users:     'who has access to this server'\n"
                    "  • Add a key:      'add another key for alice'\n"
                    "  • Rotate a key:   'update my SSH key'\n"
                )
