from loguru import logger
from app.executors.base_executor import BaseExecutor


class PM2Tool:
    """
    Tool for managing Node.js / Next.js / NestJS applications via PM2.
    PM2 is the process manager for Node-based apps (equivalent to systemd for Python).
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def install(self) -> str:
        """Installs PM2 globally via npm and updates the daemon."""
        exit_code, out, err = self.executor.execute("sudo npm install -g pm2")
        if exit_code != 0:
            raise RuntimeError(f"Failed to install PM2:\n{err}")
        # Sync daemon version to avoid "in-memory PM2 is out-of-date" warning
        self.executor.execute("pm2 update")
        logger.info("PM2 installed and updated successfully.")
        return "PM2 installed successfully."

    def start(
        self,
        app_name: str,
        script: str,
        working_directory: str,
        interpreter: str = "node",
    ) -> str:
        """
        Starts an app with PM2.
        For Next.js apps, use script='npm' and the args 'run start' will be added automatically.
        """
        # For Next.js: pm2 start npm --name app -- run start
        if script in ("npm", "yarn") or script.endswith("npm"):
            cmd = (
                f"cd {working_directory} && "
                f"pm2 start npm --name {app_name} -- run start"
            )
        else:
            cmd = (
                f"cd {working_directory} && "
                f"pm2 start {script} --name {app_name} "
                f"--interpreter {interpreter} "
                f"--cwd {working_directory}"
            )

        # Delete existing instance first to avoid duplicates
        self.executor.execute(f"pm2 delete {app_name} 2>/dev/null || true")

        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to start {app_name} with PM2:\n{err}\n{out}")
        logger.info(f"PM2 started {app_name}.")
        return f"App '{app_name}' started with PM2.\n{out}"

    def stop(self, app_name: str) -> str:
        """Stops a PM2 process."""
        exit_code, out, err = self.executor.execute(f"pm2 stop {app_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to stop {app_name}:\n{err}")
        return f"PM2 process '{app_name}' stopped."

    def restart(self, app_name: str) -> str:
        """Restarts a PM2 process."""
        exit_code, out, err = self.executor.execute(f"pm2 restart {app_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart {app_name}:\n{err}")
        return f"PM2 process '{app_name}' restarted."

    def delete(self, app_name: str) -> str:
        """Removes a PM2 process from the list."""
        exit_code, out, err = self.executor.execute(f"pm2 delete {app_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to delete {app_name}:\n{err}")
        return f"PM2 process '{app_name}' deleted."

    def save(self) -> str:
        """
        Saves the current PM2 process list so it survives server reboots.
        Always call this after starting an app.
        """
        exit_code, out, err = self.executor.execute("pm2 save")
        if exit_code != 0:
            raise RuntimeError(f"Failed to save PM2 process list:\n{err}")
        logger.info("PM2 process list saved.")
        return "PM2 process list saved (will survive reboots)."

    def setup_startup(self) -> str:
        """
        Configures PM2 to start automatically on system boot.
        Run this once per server.
        """
        exit_code, out, err = self.executor.execute(
            "pm2 startup systemd -u $(whoami) --hp $HOME | tail -1 | bash"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to set up PM2 startup:\n{err}")
        logger.info("PM2 startup configured.")
        return "PM2 startup hook configured — will auto-start on reboot."

    def status(self) -> str:
        """Shows the status of all PM2 processes."""
        exit_code, out, err = self.executor.execute("pm2 list --no-color")
        return out if out else err

    def logs(self, app_name: str, lines: int = 50) -> str:
        """Fetches the last N log lines for a PM2 process."""
        exit_code, out, err = self.executor.execute(
            f"pm2 logs {app_name} --lines {lines} --no-color --nostream"
        )
        return out if out else err
