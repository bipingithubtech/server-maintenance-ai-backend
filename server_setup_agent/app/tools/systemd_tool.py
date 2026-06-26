import base64
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

    def create_service_file(
        self,
        service_name: str,
        exec_start: str,
        working_directory: str,
        description: str = "Managed application service",
        user: str = "root",
        restart_policy: str = "always"
    ) -> str:
        """
        Creates a systemd unit file for a deployed application, then reloads
        the systemd daemon so the new service is recognized.

        Args:
            service_name: Name of the service WITHOUT '.service' suffix (e.g. 'myapp').
            exec_start: The full command to start the app (e.g. 'node /opt/myapp/index.js'
                        or '/opt/myapp/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001').
            working_directory: Absolute path to the app's working directory.
            description: Human-readable description for the unit file.
            user: Linux user the service should run as.
            restart_policy: systemd Restart= policy (e.g. 'always', 'on-failure').

        Returns:
            Confirmation message including the path written.
        """
        unit_content = f"""[Unit]
Description={description}
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_directory}
ExecStart={exec_start}
Restart={restart_policy}
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

        target_path = f"/etc/systemd/system/{service_name}.service"

        # Base64 encode to avoid shell escaping issues with quotes/special chars
        # in exec_start or paths (same safe-write pattern used by NginxTool.save_config).
        encoded_content = base64.b64encode(unit_content.encode("utf-8")).decode("utf-8")
        write_cmd = f"echo '{encoded_content}' | base64 --decode | sudo tee {target_path} > /dev/null"

        exit_code, out, err = self.executor.execute(write_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to write service file {target_path}:\n{err}")

        # Reload systemd so it picks up the new/changed unit file
        reload_exit, _, reload_err = self.executor.execute("sudo systemctl daemon-reload")
        if reload_exit != 0:
            raise RuntimeError(f"Service file written but daemon-reload failed:\n{reload_err}")

        return f"Service file created at {target_path} and systemd daemon reloaded."