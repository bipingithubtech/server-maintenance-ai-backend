from app.executors.base_executor import BaseExecutor

class SystemdTool:
    """Tool for managing systemd services."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def _run_systemctl(self, action: str, service: str) -> str:
        exit_code, out, err = self.executor.execute(f"sudo systemctl {action} {service}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to {action} {service}:\n{err}")
        return out if out else f"Successfully executed: systemctl {action} {service}"

    def start_service(self, service_name: str) -> str:
        """Starts a service."""
        return self._run_systemctl("start", service_name)

    def stop_service(self, service_name: str) -> str:
        """Stops a service."""
        return self._run_systemctl("stop", service_name)

    def restart_service(self, service_name: str) -> str:
        """Restarts a service."""
        return self._run_systemctl("restart", service_name)

    def enable_service(self, service_name: str) -> str:
        """Enables a service to start on boot."""
        return self._run_systemctl("enable", service_name)

    def check_status(self, service_name: str) -> str:
        """Checks the status of a service."""
        exit_code, out, err = self.executor.execute(f"sudo systemctl status {service_name} --no-pager")
        # Do not throw on non-zero exit code because status returns >0 if stopped or failed.
        return out if out else err
