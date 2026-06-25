from app.executors.base_executor import BaseExecutor

class LogTool:
    """Tool for reading system and application logs."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def read_syslog(self, lines: int = 50) -> str:
        """Reads the tail of /var/log/syslog."""
        exit_code, out, err = self.executor.execute(f"sudo tail -n {lines} /var/log/syslog")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read syslog:\n{err}")
        return out

    def read_journalctl(self, service: str, lines: int = 50) -> str:
        """Reads logs for a specific systemd service using journalctl."""
        exit_code, out, err = self.executor.execute(f"sudo journalctl -u {service} -n {lines} --no-pager")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read journalctl for {service}:\n{err}")
        return out

    def read_file_tail(self, filepath: str, lines: int = 50) -> str:
        """Reads the tail of any arbitrary file."""
        exit_code, out, err = self.executor.execute(f"sudo tail -n {lines} {filepath}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read tail of {filepath}:\n{err}")
        return out
