"""
MaintenanceAgent — Routine server and app maintenance.

Tasks:
  update_app(app_name)     — git pull → rebuild → restart (PM2/systemd/docker)
  rotate_logs(app_name)    — truncate large logs, flush PM2 logs
  clear_disk()             — remove npm/pip/docker cache, free disk space
  restart_service(name)    — restart PM2 app / systemd service / docker container
  system_update()          — apt-get update + upgrade
  full_maintenance()       — runs all tasks in sequence

All results reported to MS Teams.
"""

import json
import re
from typing import Dict, Any, Optional, List
from loguru import logger

from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.package_tool import PackageTool
from app.tools.pm2_tool import PM2Tool
from app.tools.systemd_tool import SystemdTool
from app.services.teams_alert_service import TeamsAlerter


class MaintenanceAgent:

    def __init__(
        self,
        executor_type:   str = "local",
        executor_config: Dict[str, Any] = None,
        server_label:    Optional[str] = None,
    ):
        if executor_config is None:
            executor_config = {}
        self.executor     = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.linux        = LinuxTool(self.executor)
        self.pkg          = PackageTool(self.executor)
        self.pm2          = PM2Tool(self.executor)
        self.systemd      = SystemdTool(self.executor)
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")

    # ── helpers ────────────────────────────────────────────────────────────────

    def _run(self, cmd: str) -> str:
        logger.info(f"  [RUN] {cmd[:160]}")
        result = self.linux.run_custom_command(cmd)
        logger.info(f"  [OK]  {str(result)[:120]}")
        return result

    def _exec(self, cmd: str):
        _, out, err = self.executor.execute(cmd)
        return out.strip(), err.strip()

    def _file_exists(self, path: str) -> bool:
        code, _, _ = self.executor.execute(f"test -f {path}")
        return code == 0

    def _dir_exists(self, path: str) -> bool:
        code, _, _ = self.executor.execute(f"test -d {path}")
        return code == 0

    def _get_disk_free_pct(self) -> float:
        out, _ = self._exec("df / | awk 'NR==2{print $5}' | tr -d '%'")
        try:
            return float(out.strip())
        except Exception:
            return 0.0

    def _detect_process_manager(self, app_name: str) -> str:
        """Detect whether app is running under PM2, systemd, or docker."""
        # Check PM2
        out, _ = self._exec("pm2 jlist 2>/dev/null")
        try:
            apps = json.loads(out)
            if any(a.get("name") == app_name for a in apps):
                return "pm2"
        except Exception:
            pass

        # Check systemd
        out, _ = self._exec(f"systemctl is-active {app_name} 2>/dev/null")
        if out in ("active", "inactive", "failed"):
            return "systemd"

        # Check docker
        out, _ = self._exec(f"docker inspect {app_name} 2>/dev/null | head -1")
        if out.strip().startswith("["):
            return "docker"

        return "unknown"

    def _detect_stack(self, app_path: str) -> str:
        """Detect app stack from files in app directory."""
        if self._file_exists(f"{app_path}/package.json"):
            out, _ = self._exec(f"cat {app_path}/package.json 2>/dev/null")
            if "next" in out.lower():
                return "nextjs"
            if "nest" in out.lower():
                return "nestjs"
            return "nodejs"
        if self._file_exists(f"{app_path}/requirements.txt"):
            out, _ = self._exec(f"cat {app_path}/requirements.txt 2>/dev/null")
            if "fastapi" in out.lower():
                return "fastapi"
            if "flask" in out.lower():
                return "flask"
            if "django" in out.lower():
                return "django"
            return "python"
        return "unknown"

    # ── Task 1: Update app ─────────────────────────────────────────────────────

    def update_app(self, app_name: str) -> str:
        """
        Pull latest code from git, rebuild, and restart the service.
        Works with PM2, systemd, and docker.
        """
        logger.info(f"[MAINTENANCE] Updating app: {app_name}")
        app_path = f"/opt/{app_name}"

        if not self._dir_exists(app_path):
            return f"ERROR: {app_path} does not exist."

        results = []

        # ── git pull ───────────────────────────────────────────────────────
        try:
            out = self._run(f"git -C {app_path} pull")
            if "Already up to date" in out:
                results.append("✓ git pull: already up to date")
                logger.info("  Already up to date — skipping rebuild")
                # Still restart to pick up any .env changes
            else:
                results.append(f"✓ git pull: updated")
        except Exception as e:
            results.append(f"✗ git pull failed: {e}")
            self.alerter.critical(
                title=f"Update failed: git pull error on {app_name}",
                server=self.server_label,
                details=str(e),
            )
            return "\n".join(results)

        # ── rebuild + restart ──────────────────────────────────────────────
        pm = self._detect_process_manager(app_name)

        if pm == "docker":
            # Docker update: rebuild image → stop old → run new
            try:
                # Check Dockerfile exists
                if not self._file_exists(f"{app_path}/Dockerfile"):
                    raise RuntimeError(f"No Dockerfile at {app_path}")

                # Get current container port and env flags before stopping
                port_out, _ = self._exec(
                    f"docker inspect --format='{{{{range $p,$conf := .NetworkSettings.Ports}}}}"
                    f"{{{{$p}}}}{{{{end}}}}' {app_name} 2>/dev/null"
                )
                port_match = __import__('re').search(r'(\d+)/tcp', port_out)
                port = port_match.group(1) if port_match else "8001"

                # Get env vars from running container
                env_out, _ = self._exec(
                    f"docker inspect --format='{{{{range .Config.Env}}}}{{{{.}}}} {{{{end}}}}' "
                    f"{app_name} 2>/dev/null"
                )
                env_flags = " ".join(
                    f"-e {e.strip()}" for e in env_out.split()
                    if "=" in e and not e.startswith("PATH=")
                )

                # Stop + remove old container (keep image for rollback)
                self.executor.execute(f"docker stop {app_name} 2>/dev/null || true")
                self.executor.execute(f"docker rm {app_name} 2>/dev/null || true")

                # Build new image (tag old as backup)
                self.executor.execute(f"docker tag {app_name} {app_name}:previous 2>/dev/null || true")
                self._run(f"docker build -t {app_name} {app_path}")

                # Run new container
                self._run(
                    f"docker run -d --name {app_name} "
                    f"--restart unless-stopped "
                    f"-p {port}:{port} "
                    f"{env_flags} "
                    f"{app_name}"
                )
                results.append(f"✓ docker: rebuilt and restarted on port {port}")
            except Exception as e:
                results.append(f"✗ docker update failed: {e}")
                # Rollback to previous image
                self.executor.execute(f"docker stop {app_name} 2>/dev/null || true")
                self.executor.execute(f"docker rm {app_name} 2>/dev/null || true")
                rollback, _ = self._exec(f"docker images {app_name}:previous -q")
                if rollback:
                    self.executor.execute(
                        f"docker run -d --name {app_name} --restart unless-stopped "
                        f"-p {port}:{port} {app_name}:previous"
                    )
                    results.append(f"⚡ Rolled back to previous image")
                self.alerter.critical(
                    title=f"Docker update failed: {app_name}",
                    server=self.server_label,
                    details=str(e),
                )
            summary = "\n".join(results)
            self.alerter.info(
                title=f"{app_name} docker updated",
                server=self.server_label,
                details=summary,
            )
            return summary
                self._run(f"npm install --prefix {app_path}")
                if stack in ("nextjs", "nestjs"):
                    # Clear old build first
                    self.executor.execute(f"rm -rf {app_path}/.next {app_path}/dist 2>/dev/null")
                    self._run(f"npm run build --prefix {app_path}")
                results.append(f"✓ rebuild: npm ({stack})")

            elif stack in ("fastapi", "flask", "django", "python"):
                self._run(f"{app_path}/venv/bin/pip install -r {app_path}/requirements.txt")
                results.append("✓ rebuild: pip install")

        except Exception as e:
            results.append(f"✗ rebuild failed: {e}")
            self.alerter.critical(
                title=f"Update failed: rebuild error on {app_name}",
                server=self.server_label,
                details=str(e),
            )
            return "\n".join(results)

        # ── restart ────────────────────────────────────────────────────────
        pm = self._detect_process_manager(app_name)
        try:
            result = self.restart_service(app_name, pm)
            results.append(f"✓ restart ({pm}): done")
        except Exception as e:
            results.append(f"✗ restart failed: {e}")
            self.alerter.critical(
                title=f"Update failed: restart error on {app_name}",
                server=self.server_label,
                details=str(e),
            )
            return "\n".join(results)

        summary = "\n".join(results)
        self.alerter.info(
            title=f"{app_name} updated successfully",
            server=self.server_label,
            details=summary,
        )
        return summary

    # ── Task 2: Rotate logs ────────────────────────────────────────────────────

    def rotate_logs(self, app_name: Optional[str] = None) -> str:
        """
        Rotates logs for an app or all apps.
        - Flushes PM2 logs
        - Truncates nginx access/error logs > 50MB
        - Clears journalctl logs older than 7 days
        """
        logger.info(f"[MAINTENANCE] Rotating logs: {app_name or 'all'}")
        results = []

        # PM2 log flush
        try:
            if app_name:
                self._run(f"pm2 flush {app_name} 2>/dev/null || true")
            else:
                self._run("pm2 flush 2>/dev/null || true")
            results.append("✓ PM2 logs flushed")
        except Exception as e:
            results.append(f"⚠ PM2 flush: {e}")

        # Nginx logs — truncate if > 50MB
        for log_type in ("access", "error"):
            log_path = f"/var/log/nginx/{app_name}.{log_type}.log" if app_name else f"/var/log/nginx/{log_type}.log"
            size_out, _ = self._exec(f"stat -c%s {log_path} 2>/dev/null || echo 0")
            try:
                size_mb = int(size_out.strip()) / 1024 / 1024
                if size_mb > 50:
                    self.executor.execute(f"sudo truncate -s 0 {log_path}")
                    results.append(f"✓ Truncated {log_path} ({size_mb:.0f}MB → 0)")
            except Exception:
                pass

        # Journalctl — clear logs older than 7 days
        try:
            self._run("sudo journalctl --vacuum-time=7d 2>/dev/null || true")
            results.append("✓ journalctl: cleared logs older than 7 days")
        except Exception as e:
            results.append(f"⚠ journalctl: {e}")

        return "\n".join(results)

    # ── Task 3: Clear disk ─────────────────────────────────────────────────────

    def clear_disk(self) -> str:
        """
        Frees disk space by removing:
        - npm cache
        - pip cache
        - docker unused images/containers/volumes
        - apt cache
        - old systemd journal logs
        """
        logger.info("[MAINTENANCE] Clearing disk space")
        results = []

        disk_before = self._get_disk_free_pct()

        cleanups = [
            ("npm cache",    "npm cache clean --force 2>/dev/null || true"),
            ("pip cache",    "pip cache purge 2>/dev/null || true"),
            ("apt cache",    "sudo apt-get clean -y 2>/dev/null || true"),
            ("apt autoremove", "sudo apt-get autoremove -y 2>/dev/null || true"),
            ("journalctl",   "sudo journalctl --vacuum-size=100M 2>/dev/null || true"),
            ("tmp files",    "sudo find /tmp -type f -atime +7 -delete 2>/dev/null || true"),
        ]

        # Docker cleanup only if installed
        code, _, _ = self.executor.execute("which docker 2>/dev/null")
        if code == 0:
            cleanups += [
                ("docker containers", "docker container prune -f 2>/dev/null || true"),
                ("docker images",     "docker image prune -f 2>/dev/null || true"),
                ("docker volumes",    "docker volume prune -f 2>/dev/null || true"),
            ]

        for label, cmd in cleanups:
            try:
                self.executor.execute(cmd)
                results.append(f"✓ {label}")
            except Exception as e:
                results.append(f"⚠ {label}: {e}")

        disk_after = self._get_disk_free_pct()
        results.append(f"\nDisk usage: {disk_before:.0f}% → {disk_after:.0f}%")

        if disk_after > 90:
            self.alerter.warning(
                title="Disk still critically high after cleanup",
                server=self.server_label,
                details=f"Disk usage: {disk_after:.0f}%",
            )
        else:
            self.alerter.info(
                title="Disk cleanup complete",
                server=self.server_label,
                details=f"Disk: {disk_before:.0f}% → {disk_after:.0f}%",
            )

        return "\n".join(results)

    # ── Task 4: Restart service ────────────────────────────────────────────────

    def restart_service(self, name: str, manager: Optional[str] = None) -> str:
        """
        Restarts a service by name.
        Auto-detects PM2 / systemd / docker if manager not specified.
        """
        if not manager:
            manager = self._detect_process_manager(name)

        logger.info(f"[MAINTENANCE] Restarting {name} via {manager}")

        if manager == "pm2":
            out, err = self._exec(f"pm2 restart {name}")
            if err and "error" in err.lower():
                raise RuntimeError(err)
            return f"PM2 app '{name}' restarted."

        elif manager == "systemd":
            exit_code, out, err = self.executor.execute(f"sudo systemctl restart {name}")
            if exit_code != 0:
                raise RuntimeError(f"systemctl restart failed: {err}")
            return f"Systemd service '{name}' restarted."

        elif manager == "docker":
            exit_code, out, err = self.executor.execute(f"docker restart {name}")
            if exit_code != 0:
                raise RuntimeError(f"docker restart failed: {err}")
            return f"Docker container '{name}' restarted."

        else:
            raise RuntimeError(f"Cannot detect process manager for '{name}'. Specify: pm2, systemd, or docker.")

    # ── Task 5: System update ──────────────────────────────────────────────────

    def system_update(self) -> str:
        """
        Updates system packages via apt-get.
        Sends Teams alert on completion.
        """
        logger.info("[MAINTENANCE] Running system update")
        results = []

        try:
            self._run("sudo apt-get update -y")
            results.append("✓ apt-get update")
        except Exception as e:
            results.append(f"✗ apt-get update: {e}")
            return "\n".join(results)

        try:
            self._run("sudo apt-get upgrade -y")
            results.append("✓ apt-get upgrade")
        except Exception as e:
            results.append(f"✗ apt-get upgrade: {e}")

        summary = "\n".join(results)
        self.alerter.info(
            title="System packages updated",
            server=self.server_label,
            details=summary,
        )
        return summary

    # ── Task 6: Full maintenance ───────────────────────────────────────────────

    def full_maintenance(self, app_names: Optional[List[str]] = None) -> str:
        """
        Runs all maintenance tasks:
        1. Update all apps (or specified list)
        2. Rotate logs
        3. Clear disk
        4. System update

        Sends a single summary Teams alert.
        """
        logger.info("[MAINTENANCE] Running full maintenance")
        report = ["=" * 50, "  MAINTENANCE REPORT", "=" * 50]

        # Detect all PM2 apps if none specified
        if not app_names:
            out, _ = self._exec("pm2 jlist 2>/dev/null")
            try:
                apps = json.loads(out)
                app_names = [a.get("name") for a in apps if a.get("name")]
            except Exception:
                app_names = []

        # Update apps
        report.append("\n── App Updates ──────────────────────────")
        for name in app_names:
            report.append(f"\n  {name}:")
            result = self.update_app(name)
            for line in result.splitlines():
                report.append(f"    {line}")

        # Rotate logs
        report.append("\n── Log Rotation ─────────────────────────")
        result = self.rotate_logs()
        for line in result.splitlines():
            report.append(f"  {line}")

        # Clear disk
        report.append("\n── Disk Cleanup ─────────────────────────")
        result = self.clear_disk()
        for line in result.splitlines():
            report.append(f"  {line}")

        # System update
        report.append("\n── System Update ────────────────────────")
        result = self.system_update()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n" + "=" * 50)
        full_report = "\n".join(report)

        self.alerter.info(
            title="Full maintenance completed",
            server=self.server_label,
            details=f"Apps updated: {', '.join(app_names) if app_names else 'none'}",
        )

        return full_report
