from app.executors.base_executor import BaseExecutor

class FirewallTool:
    """Tool for managing UFW (Uncomplicated Firewall)."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def enable(self) -> str:
        """Enables the firewall."""
        exit_code, out, err = self.executor.execute("echo 'y' | sudo ufw enable")
        if exit_code != 0:
            raise RuntimeError(f"Failed to enable UFW:\n{err}")
        return out

    def disable(self) -> str:
        """Disables the firewall."""
        exit_code, out, err = self.executor.execute("sudo ufw disable")
        if exit_code != 0:
            raise RuntimeError(f"Failed to disable UFW:\n{err}")
        return out

    def allow_port(self, port: str, protocol: str = "tcp") -> str:
        """Allows traffic on a specific port and protocol."""
        exit_code, out, err = self.executor.execute(f"sudo ufw allow {port}/{protocol}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to allow port {port}/{protocol}:\n{err}")
        return out

    def deny_port(self, port: str, protocol: str = "tcp") -> str:
        """Denies traffic on a specific port and protocol."""
        exit_code, out, err = self.executor.execute(f"sudo ufw deny {port}/{protocol}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to deny port {port}/{protocol}:\n{err}")
        return out

    def status(self) -> str:
        """Gets the current status of the firewall."""
        exit_code, out, err = self.executor.execute("sudo ufw status verbose")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get UFW status:\n{err}")
        return out
