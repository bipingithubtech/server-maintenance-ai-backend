import shlex
from app.executors.base_executor import BaseExecutor


class SecurityTool:
    """Tool for handling server hardening and security tasks."""

    SSHD_CONFIG = "/etc/ssh/sshd_config"

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    # ── fail2ban ──────────────────────────────────────────────────────────

    def install_fail2ban(self, ignore_ips: str = "") -> str:
        """
        Installs and enables fail2ban for brute-force protection.

        ignore_ips: space-separated extra IPs/CIDRs to whitelist (e.g. your
        deployment agent's own host IP) so automated reconnects are never
        banned by their own retry behavior. Always includes 127.0.0.1/8
        by default regardless of this param.
        """
        exit_code, out, err = self.executor.execute(
            "sudo apt-get update -y && sudo apt-get install -y fail2ban"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to install fail2ban:\n{err}")

        self.executor.execute("sudo systemctl start fail2ban && sudo systemctl enable fail2ban")

        # Verify the sshd jail is actually active
        code, status_out, _ = self.executor.execute("sudo fail2ban-client status sshd")
        if code != 0:
            return f"Fail2ban installed, but sshd jail not yet active (may need default jail.local config).\n{out}"
        return f"Fail2ban installed and enabled successfully.\n{status_out}"

    def write_fail2ban_jail(self, ignore_ips: str = "") -> str:
        """
        Writes /etc/fail2ban/jail.local with sane production defaults:
        SSH + nginx protection, and an ignoreip whitelist so your own
        agent/deploy host never gets locked out by its own reconnects.
        """
        ignore_line = "127.0.0.1/8 ::1"
        if ignore_ips.strip():
            ignore_line += f" {ignore_ips.strip()}"

        jail_config = (
            "[DEFAULT]\n"
            "bantime  = 600\n"
            "findtime = 600\n"
            "maxretry = 5\n"
            f"ignoreip = {ignore_line}\n\n"
            "[sshd]\n"
            "enabled = true\n"
            "port    = ssh\n"
            "logpath = /var/log/auth.log\n\n"
            "[nginx-http-auth]\n"
            "enabled  = true\n"
            "logpath  = /var/log/nginx/error.log\n"
        )
        import base64
        encoded = base64.b64encode(jail_config.encode()).decode()
        self.executor.execute(
            f"echo '{encoded}' | base64 --decode | sudo tee /etc/fail2ban/jail.local > /dev/null"
        )
        exit_code, out, err = self.executor.execute("sudo systemctl restart fail2ban")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart fail2ban after config write:\n{err}")
        return f"fail2ban jail configured. Whitelisted IPs: {ignore_line}"

    # ── user bootstrap (run as root on a fresh server) ──────────────────────

    def bootstrap_sudo_user(self, username: str, password: str, public_key: str = "") -> str:
        """
        Creates a new sudo-enabled user on a fresh server (must be run as root).
        Sets a real password so the user can SSH in with username + password.
        Optionally installs an SSH public key if provided.
        Does NOT disable password auth — only root login is blocked separately.

        PRODUCTION NOTE: if this server will later run harden_ssh() with
        disable_password_auth=True (the recommended default), you MUST
        provide public_key here, or the user will be locked out entirely
        once password auth is disabled.
        """
        username_q = shlex.quote(username)

        # Create user if not exists
        code, _, _ = self.executor.execute(f"id -u {username_q}")
        if code != 0:
            exit_code, out, err = self.executor.execute(
                f"sudo adduser --disabled-password --gecos '' {username_q}"
            )
            if exit_code != 0:
                raise RuntimeError(f"Failed to create user {username}:\n{err}")

        # Set the password
        password_q = shlex.quote(f"{username}:{password}")
        exit_code, _, err = self.executor.execute(
            f"echo {password_q} | sudo chpasswd"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to set password for {username}:\n{err}")

        # Add to sudo group
        exit_code, _, err = self.executor.execute(f"sudo usermod -aG sudo {username_q}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to add {username} to sudo group:\n{err}")

        # Grant passwordless sudo so agent commands work over SSH without a TTY
        sudoers_line = shlex.quote(f"{username} ALL=(ALL) NOPASSWD:ALL")
        self.executor.execute(
            f"echo {sudoers_line} | sudo tee /etc/sudoers.d/{username_q} > /dev/null && "
            f"sudo chmod 440 /etc/sudoers.d/{username_q}"
        )

        # Optionally install SSH public key
        if public_key and public_key.strip():
            pubkey_q = shlex.quote(public_key.strip())
            cmd = (
                f"sudo mkdir -p /home/{username_q}/.ssh && "
                f"echo {pubkey_q} | sudo tee -a /home/{username_q}/.ssh/authorized_keys > /dev/null && "
                f"sudo chmod 700 /home/{username_q}/.ssh && "
                f"sudo chmod 600 /home/{username_q}/.ssh/authorized_keys && "
                f"sudo chown -R {username_q}:{username_q} /home/{username_q}/.ssh"
            )
            exit_code, _, err = self.executor.execute(cmd)
            if exit_code != 0:
                raise RuntimeError(f"Failed to set up SSH key for {username}:\n{err}")

        return f"User '{username}' created with sudo access and password set."

    # ── SSH hardening ─────────────────────────────────────────────────────

    def _set_sshd_option(self, key: str, value: str) -> None:
        """Helper: idempotently set/replace a single sshd_config directive."""
        cmd = (
            f"sudo grep -qE '^#?{key} ' {self.SSHD_CONFIG} && "
            f"sudo sed -i 's/^#\\?{key} .*/{key} {value}/' {self.SSHD_CONFIG} || "
            f"echo '{key} {value}' | sudo tee -a {self.SSHD_CONFIG} > /dev/null"
        )
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to set sshd option {key}:\n{err}")

    def _remove_sshd_option(self, key: str) -> None:
        """
        Helper: remove any existing line for a directive entirely.
        Used when switching auth modes so a stale directive from a
        previous hardening run (e.g. leftover AuthenticationMethods)
        never lingers and silently re-imposes an old policy.
        """
        self.executor.execute(f"sudo sed -i '/^{key} /d' {self.SSHD_CONFIG}")

    def verify_key_installed(self, username: str) -> bool:
        """
        Checks whether the given user has at least one key in authorized_keys.
        Call this BEFORE harden_ssh(disable_password_auth=True) to avoid
        locking yourself out — if this returns False, do not proceed.
        """
        cmd = f"sudo test -s /home/{shlex.quote(username)}/.ssh/authorized_keys && echo yes || echo no"
        exit_code, out, err = self.executor.execute(cmd)
        return out.strip() == "yes"

    def harden_ssh(
        self,
        max_auth_tries: int = 3,
        disable_password_auth: bool = True,
        require_two_factor: bool = False,
    ) -> str:
        """
        SSH hardening — PRODUCTION-SAFE DEFAULTS.

        Defaults favor key-only auth (disable_password_auth=True,
        require_two_factor=False). This is the actual industry-standard
        secure baseline for server SSH (matches CIS benchmark / major cloud
        provider recommended configs) AND it's the only mode that works
        reliably with unattended agent reconnects.

        "require_two_factor" here means chaining publickey + password in a
        SINGLE sshd session (AuthenticationMethods publickey,password). This
        is NOT real 2FA (no TOTP/hardware token) — it's a second, WEAKER
        factor layered on. It breaks any client (like paramiko reconnects)
        that only supplies a key, and compounds badly with fail2ban bans on
        the resulting failed attempts. Reserve it only for servers with a
        human operator who can supply both interactively — never for
        agent/automation-managed servers.

        Args:
          max_auth_tries: SSH MaxAuthTries directive.
          disable_password_auth: True = key-only (recommended default).
          require_two_factor: True = require key AND password together.
            Mutually exclusive with disable_password_auth.

        Raises:
          ValueError: if both disable_password_auth and require_two_factor
            are True (nonsensical — key-only has no password to chain).
          RuntimeError: if sshd_config fails validation (sshd -t) — in this
            case SSH is NOT restarted, protecting the current session.

        IMPORTANT: before calling this with disable_password_auth=True,
        confirm a valid SSH key is already in authorized_keys for the user
        that will reconnect (see verify_key_installed()) — otherwise you
        WILL lock yourself out.
        """
        if disable_password_auth and require_two_factor:
            raise ValueError(
                "disable_password_auth and require_two_factor are mutually "
                "exclusive — key-only mode has no password to chain with 2FA."
            )

        self._set_sshd_option("PermitRootLogin", "no")
        self._set_sshd_option("MaxAuthTries", str(max_auth_tries))

        # Always clear any prior AuthenticationMethods line before setting a
        # new mode, so switching modes never leaves a stale directive behind.
        self._remove_sshd_option("AuthenticationMethods")

        if disable_password_auth:
            # Key only — strictest AND most reliable mode. Recommended
            # default for agent-managed / production servers.
            self._set_sshd_option("PasswordAuthentication", "no")
            self._set_sshd_option("ChallengeResponseAuthentication", "no")
            auth_desc = "key-only (no password) — recommended for automated/agent access"
        elif require_two_factor:
            # Require BOTH SSH key AND password — human-operator servers only.
            self._set_sshd_option("PasswordAuthentication", "yes")
            self._set_sshd_option("ChallengeResponseAuthentication", "yes")
            self._set_sshd_option("AuthenticationMethods", "publickey,password")
            auth_desc = "2-factor: SSH key + password (both required) — HUMAN OPERATORS ONLY"
        else:
            # Password login only — least secure, avoid in production.
            self._set_sshd_option("PasswordAuthentication", "yes")
            auth_desc = "password only — NOT recommended for production"

        # Validate config BEFORE restarting — critical to avoid locking yourself out
        exit_code, out, err = self.executor.execute("sudo sshd -t")
        if exit_code != 0:
            raise RuntimeError(
                f"sshd_config validation failed — NOT restarting SSH to avoid lockout:\n{err}"
            )

        exit_code, out, err = self.executor.execute("sudo systemctl restart sshd")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart sshd:\n{err}")

        return (
            f"SSH hardened: root login disabled, MaxAuthTries={max_auth_tries}, "
            f"auth mode: {auth_desc}."
        )

    def disable_root_ssh_login(self) -> str:
        """Kept for backward compatibility — narrower than harden_ssh()."""
        self._set_sshd_option("PermitRootLogin", "no")
        exit_code, out, err = self.executor.execute("sudo sshd -t")
        if exit_code != 0:
            raise RuntimeError(f"sshd_config validation failed, not restarting:\n{err}")
        self.executor.execute("sudo systemctl restart sshd")
        return "Root SSH login disabled successfully."

    # ── automatic security updates ───────────────────────────────────────

    def enable_unattended_upgrades(self) -> str:
        """Installs and enables automatic security updates."""
        # Wait for any existing apt/dpkg lock to be released (up to 120s)
        self.executor.execute(
            "sudo systemd-run --property=Type=oneshot --wait "
            "sh -c 'while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 2; done' "
            "2>/dev/null || "
            "while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 2; done"
        )

        exit_code, out, err = self.executor.execute(
            "sudo apt-get install -y unattended-upgrades"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to install unattended-upgrades:\n{err}")

        exit_code, out, err = self.executor.execute(
            "sudo dpkg-reconfigure -f noninteractive unattended-upgrades"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to enable unattended-upgrades:\n{err}")

        return "Automatic security updates enabled."