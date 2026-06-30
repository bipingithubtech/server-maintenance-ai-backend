import shlex
from app.executors.base_executor import BaseExecutor


class SecurityTool:
    """Tool for handling server hardening and security tasks."""

    SSHD_CONFIG = "/etc/ssh/sshd_config"

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    # ── fail2ban ──────────────────────────────────────────────────────────

    def install_fail2ban(self) -> str:
        """Installs and enables fail2ban for brute-force protection."""
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

    # ── user bootstrap (run as root on a fresh server) ──────────────────────

    def bootstrap_sudo_user(self, username: str, public_key: str) -> str:
        """
        Creates a new sudo-enabled user on a fresh server (must be run as root),
        and installs the given SSH public key for that user.
        Does NOT touch root login — that's a separate explicit step (harden_ssh).
        """
        username_q = shlex.quote(username)
        pubkey_q = shlex.quote(public_key.strip())

        # Create user if not exists
        code, _, _ = self.executor.execute(f"id -u {username_q}")
        if code != 0:
            exit_code, out, err = self.executor.execute(
                f"adduser --disabled-password --gecos '' {username_q}"
            )
            if exit_code != 0:
                raise RuntimeError(f"Failed to create user {username}:\n{err}")

        # Add to sudo group
        exit_code, out, err = self.executor.execute(f"usermod -aG sudo {username_q}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to add {username} to sudo group:\n{err}")

        # Set up SSH key for the new user
        cmd = (
            f"mkdir -p /home/{username_q}/.ssh && "
            f"echo {pubkey_q} >> /home/{username_q}/.ssh/authorized_keys && "
            f"chmod 700 /home/{username_q}/.ssh && "
            f"chmod 600 /home/{username_q}/.ssh/authorized_keys && "
            f"chown -R {username_q}:{username_q} /home/{username_q}/.ssh"
        )
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to set up SSH key for {username}:\n{err}")

        return f"User '{username}' created, added to sudo group, SSH key installed."

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

    def harden_ssh(self, max_auth_tries: int = 3, disable_password_auth: bool = True) -> str:
        """
        Full SSH hardening:
          - disable root login
          - (optionally) disable password auth, key-only login
          - set MaxAuthTries
          - validate config before restarting (avoids self-lockout)
        """
        self._set_sshd_option("PermitRootLogin", "no")
        self._set_sshd_option("MaxAuthTries", str(max_auth_tries))
        if disable_password_auth:
            self._set_sshd_option("PasswordAuthentication", "no")
            self._set_sshd_option("ChallengeResponseAuthentication", "no")

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
            f"password auth {'disabled' if disable_password_auth else 'left enabled'}."
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
        exit_code, out, err = self.executor.execute(
            "sudo apt-get update -y && sudo apt-get install -y unattended-upgrades"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to install unattended-upgrades:\n{err}")

        exit_code, out, err = self.executor.execute(
            "sudo dpkg-reconfigure -f noninteractive unattended-upgrades"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to enable unattended-upgrades:\n{err}")

        return "Automatic security updates enabled."