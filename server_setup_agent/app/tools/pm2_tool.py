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
        # Check if PM2 is already installed
        code, _, _ = self.executor.execute("which pm2")
        if code == 0:
            logger.info("✓ PM2 is already installed (skipping installation).")
            return "PM2 is already installed (skipping)."
        
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
        port: str = "",
    ) -> str:
        """
        Starts an app with PM2.
        For Next.js/NestJS apps, use script='npm' and will auto-detect start script.
        Detects 'start:prod' first (production), then 'start' (default).
        If port is provided, it is injected via the PORT env var so Next.js / Node honours it.
        """
        port_env = f"PORT={port} " if port else ""

        # For Next.js/NestJS: pm2 start npm --name app -- run start (or start:prod)
        if script in ("npm", "yarn") or script.endswith("npm"):
            # Auto-detect if start:prod exists in package.json
            start_script = "start"
            pkg_json_path = f"{working_directory}/package.json"
            
            # Check if start:prod script exists
            check_cmd = f"grep -q '\"start:prod\"' {pkg_json_path} 2>/dev/null && echo 'exists' || echo 'not found'"
            _, check_out, _ = self.executor.execute(check_cmd)
            if "exists" in check_out:
                start_script = "start:prod"
                logger.info(f"[PM2] Detected start:prod script for {app_name}")
            
            cmd = (
                f"cd {working_directory} && "
                f"{port_env}pm2 start npm --name {app_name} -- run {start_script}"
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
        # SAFETY: This is a destructive operation
        logger.critical(f"[SECURITY] DELETE PM2 APP ATTEMPT: {app_name}")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="⚠️ DELETE PM2 APP REQUESTED",
            server="Server",
            details=f"PM2 app deletion request: {app_name}\n\n"
                   f"This will remove the process from PM2 permanently.\n"
                   f"REQUIRES EXPLICIT CONFIRMATION from administrator."
        )
        
        raise RuntimeError(
            f"❌ BLOCKED: PM2 app deletion is a destructive operation.\n\n"
            f"App to delete: {app_name}\n\n"
            f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
            f"To proceed, you must:\n"
            f"1. Verify this is intentional\n"
            f"2. Get explicit approval from team lead\n"
            f"3. Run manually via SSH:\n"
            f"   pm2 delete {app_name}"
        )

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
