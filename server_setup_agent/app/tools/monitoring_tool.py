from app.executors.base_executor import BaseExecutor

class MonitoringTool:
    """Tool for monitoring system resources."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def get_cpu_usage(self) -> str:
        """Gets CPU usage snapshot using top."""
        exit_code, out, err = self.executor.execute("top -bn1 | grep 'Cpu(s)'")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get CPU usage:\n{err}")
        return out.strip()

    def get_ram_usage(self) -> str:
        """Gets RAM usage snapshot using free."""
        exit_code, out, err = self.executor.execute("free -m")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get RAM usage:\n{err}")
        return out.strip()

    def get_disk_usage(self) -> str:
        """Gets Disk usage snapshot using df."""
        exit_code, out, err = self.executor.execute("df -h")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get disk usage:\n{err}")
        return out.strip()
