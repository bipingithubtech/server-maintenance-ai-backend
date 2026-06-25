from app.executors.base_executor import BaseExecutor

class SecurityTool:
    """Tool for handling server hardening and security tasks."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def install_fail2ban(self) -> str:
        """Installs and enables fail2ban for brute-force protection."""
        exit_code, out, err = self.executor.execute("sudo apt-get update -y && sudo apt-get install -y fail2ban")
        if exit_code != 0:
            raise RuntimeError(f"Failed to install fail2ban:\n{err}")
        
        # Start and enable
        self.executor.execute("sudo systemctl start fail2ban && sudo systemctl enable fail2ban")
        return f"Fail2ban installed and enabled successfully.\n{out}"

    def disable_root_ssh_login(self) -> str:
        """Disables root login via SSH by modifying sshd_config."""
        cmd = "sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && sudo systemctl restart sshd"
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to disable root SSH login:\n{err}")
        return "Root SSH login disabled successfully."
