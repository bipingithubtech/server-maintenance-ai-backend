"""
MonitoringAgent — Health check and auto-recovery for deployed apps.

Responsibilities:
  - Check system resources (CPU, RAM, disk)
  - Check all PM2 apps are online
  - Check nginx is serving correctly
  - Hit HTTP endpoints to verify apps respond
  - Auto-restart errored/stopped PM2 apps
  - Report full health summary

Usage:
    agent = MonitoringAgent(executor_type="ssh", executor_config={...})
    report = agent.check_all()          # full health check
    report = agent.check_app("ats-backend")   # single app check
"""

import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from loguru import logger

from app.executors.executor_factory import ExecutorFactory
from app.tools.monitoring_tool import MonitoringTool
from app.tools.log_tool import LogTool
from app.services.teams_alert_service import TeamsAlerter


# ── Thresholds ─────────────────────────────────────────────────────────────────
CPU_WARN_PCT   = 85    # warn above this %
RAM_WARN_PCT   = 85
DISK_WARN_PCT  = 85
RESTART_WARN   = 5     # warn if app restarted more than this many times


# ── Health result dataclass ────────────────────────────────────────────────────

@dataclass
class AppHealth:
    name:        str
    status:      str          # online | errored | stopped | not_found
    restarts:    int  = 0
    cpu:         str  = "0%"
    memory:      str  = "0mb"
    port:        Optional[str] = None
    http_ok:     bool = False
    http_code:   str  = ""
    issues:      List[str] = field(default_factory=list)
    auto_fixed:  bool = False


@dataclass
class SystemHealth:
    cpu:         str = ""
    ram:         str = ""
    disk:        str = ""
    uptime:      str = ""
    nginx_status: str = ""


# ── Agent ──────────────────────────────────────────────────────────────────────

class MonitoringAgent:

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None,
                 server_label: Optional[str] = None):
        if executor_config is None:
            executor_config = {}
        self.executor     = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.mon          = MonitoringTool(self.executor)
        self.log          = LogTool(self.executor)
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")

    # ── System checks ──────────────────────────────────────────────────────────

    def _check_system(self) -> SystemHealth:
        health = SystemHealth()
        try:
            health.cpu    = self.mon.get_cpu_usage()
            health.ram    = self.mon.get_ram_usage()
            health.disk   = self.mon.get_disk_usage()
            health.uptime = self.mon.get_system_uptime()

            # Fire Teams alerts on high resource usage
            import re as _re
            for label, value, threshold in [
                ("CPU",  health.cpu,  CPU_WARN_PCT),
                ("RAM",  health.ram,  RAM_WARN_PCT),
                ("Disk", health.disk, DISK_WARN_PCT),
            ]:
                pct_match = _re.search(r'(\d+(?:\.\d+)?)\s*%', value)
                if pct_match and float(pct_match.group(1)) >= threshold:
                    self.alerter.warning(
                        title=f"High {label} usage: {value}",
                        server=self.server_label,
                        details=f"{label} exceeded {threshold}% threshold",
                    )
        except Exception as e:
            logger.warning(f"  [SYSTEM] resource check failed: {e}")

        try:
            health.nginx_status = self.mon.get_nginx_status()
            if health.nginx_status != "active":
                self.alerter.critical(
                    title="Nginx is DOWN",
                    server=self.server_label,
                    details=f"nginx status: {health.nginx_status}",
                )
        except Exception as e:
            logger.warning(f"  [NGINX] status check failed: {e}")
        return health

    # ── Single app check ───────────────────────────────────────────────────────

    def _check_app(self, app_name: str, port: Optional[str] = None, domain: Optional[str] = None) -> AppHealth:
        health = AppHealth(name=app_name, status="unknown")

        # PM2 status
        try:
            pm2 = self.mon.get_pm2_app_status(app_name)
            health.status   = pm2.get("status", "unknown")
            health.restarts = pm2.get("restarts", 0)
            health.cpu      = pm2.get("cpu", "0%")
            health.memory   = pm2.get("memory", "0mb")
        except Exception as e:
            health.issues.append(f"PM2 check failed: {e}")

        # Port listening check
        if port:
            health.port = port
            listening = self.mon.check_port_listening(port)
            if not listening:
                health.issues.append(f"Port {port} is NOT listening")
            else:
                logger.info(f"  [PORT] {port} is listening ✓")

        # HTTP health check
        base = domain or "127.0.0.1"
        if port:
            url = f"http://{base}:{port}/"
        else:
            url = f"http://{base}/"

        try:
            resp = self.mon.check_http_endpoint(url)
            health.http_ok   = resp["reachable"]
            health.http_code = resp["status_code"]
            if not health.http_ok:
                health.issues.append(
                    f"HTTP {resp['status_code']} from {url}"
                )
        except Exception as e:
            health.issues.append(f"HTTP check failed: {e}")

        # Auto-restart if errored or stopped
        if health.status in ("errored", "stopped"):
            logger.warning(f"  [AUTO-RESTART] {app_name} is {health.status} — restarting...")
            # Send alert BEFORE restart attempt
            self.alerter.critical(
                title=f"{app_name} is {health.status.upper()}",
                server=self.server_label,
                details=f"PM2 status: {health.status} — attempting auto-restart",
            )
            try:
                self.mon.restart_pm2_app(app_name)
                health.auto_fixed = True
                health.issues.append(f"Auto-restarted (was {health.status})")
                health.status = "restarted"
                self.alerter.info(
                    title=f"{app_name} auto-restarted",
                    server=self.server_label,
                    details=f"App was {health.status} — PM2 restart triggered automatically",
                )
            except Exception as e:
                health.issues.append(f"Auto-restart failed: {e}")
                self.alerter.critical(
                    title=f"{app_name} is DOWN — auto-restart failed",
                    server=self.server_label,
                    details=str(e),
                )

        # Warn on high restart count
        if health.restarts > RESTART_WARN:
            health.issues.append(f"High restart count: {health.restarts}")
            self.alerter.warning(
                title=f"{app_name} restarting frequently",
                server=self.server_label,
                details=f"Restart count: {health.restarts} (threshold: {RESTART_WARN})",
            )

        # Alert on HTTP failure
        if not health.http_ok and health.http_code:
            self.alerter.critical(
                title=f"{app_name} HTTP check failed",
                server=self.server_label,
                details=f"Got HTTP {health.http_code} from {url}",
            )

        return health

    # ── Detect all deployed apps from PM2 ─────────────────────────────────────

    def _get_all_pm2_apps(self) -> List[str]:
        """Returns list of all app names currently registered in PM2 using JSON output."""
        _, jout, _ = self.executor.execute("pm2 jlist 2>/dev/null")
        try:
            data = json.loads(jout)
            return [a.get("name", "") for a in data if a.get("name")]
        except Exception:
            pass

        # Fallback: parse text output — look for lines with app name pattern
        _, out, _ = self.executor.execute("pm2 list --no-color 2>/dev/null")
        apps = []
        for line in out.splitlines():
            # Match lines like: │ 0  │ ats-frontend   │ fork  │ ...
            m = re.search(r'│\s*\d+\s*│\s*([\w\-]+)\s*│', line)
            if m:
                name = m.group(1).strip()
                if name and name not in ("id", "name", "mode"):
                    apps.append(name)
        return apps

    def _get_app_port(self, app_name: str) -> Optional[str]:
        """
        Try to detect the port an app is listening on from:
          1. /opt/<app_name>/.env PORT=
          2. ss -tlnp (node processes)
          3. deployment_context.json
        """
        # Check .env
        _, env_out, _ = self.executor.execute(
            f"grep -i '^PORT=' /opt/{app_name}/.env 2>/dev/null | head -1"
        )
        if env_out.strip():
            port = env_out.strip().split("=", 1)[-1].strip().strip('"').strip("'")
            if port.isdigit():
                return port

        # Check ss -tlnp
        _, ss_out, _ = self.executor.execute("ss -tlnp | grep node")
        import re
        match = re.search(r':(\d{3,5})\s', ss_out)
        if match:
            return match.group(1)

        # Check deployment_context.json (local file)
        try:
            with open("deployment_context.json") as f:
                ctx = json.load(f)
                if ctx.get("app_name") == app_name:
                    return ctx.get("port")
        except Exception:
            pass

        return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_app(self, app_name: str, port: Optional[str] = None, domain: Optional[str] = None) -> str:
        """Run health check on a single app and return a human-readable report."""
        logger.info(f"[MONITOR] Checking app: {app_name}")

        if not port:
            port = self._get_app_port(app_name)

        sys_health = self._check_system()
        app_health = self._check_app(app_name, port, domain)

        return self._format_report(sys_health, [app_health])

    def check_all(self, domain: Optional[str] = None) -> str:
        """Run health check on all PM2 apps + system resources."""
        logger.info("[MONITOR] Running full health check...")

        sys_health = self._check_system()
        apps = self._get_all_pm2_apps()

        if not apps:
            logger.warning("  [MONITOR] No PM2 apps found.")

        app_results = []
        for app_name in apps:
            logger.info(f"  [MONITOR] Checking {app_name}...")
            port = self._get_app_port(app_name)
            app_health = self._check_app(app_name, port, domain)
            app_results.append(app_health)

        return self._format_report(sys_health, app_results)

    # ── Detailed queries ───────────────────────────────────────────────────────

    def get_logs(self, app_name: str, lines: int = 50) -> str:
        """Get recent logs for an app."""
        return self.mon.get_pm2_logs(app_name, lines)

    def get_nginx_errors(self, app_name: Optional[str] = None) -> str:
        """Get nginx error logs."""
        if app_name:
            return self.mon.get_app_nginx_errors(app_name)
        return self.mon.get_nginx_errors()

    def get_system_summary(self) -> str:
        """Get system resource summary only."""
        h = self._check_system()
        return (
            f"CPU:    {h.cpu}\n"
            f"RAM:    {h.ram}\n"
            f"Disk:   {h.disk}\n"
            f"Uptime: {h.uptime}\n"
            f"Nginx:  {h.nginx_status}"
        )

    # ── Report formatter ───────────────────────────────────────────────────────

    def _format_report(self, sys: SystemHealth, apps: List[AppHealth]) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("  SERVER HEALTH REPORT")
        lines.append("=" * 60)

        # System
        lines.append("\n── System ──────────────────────────────────")
        lines.append(f"  CPU:    {sys.cpu}")
        lines.append(f"  RAM:    {sys.ram}")
        lines.append(f"  Disk:   {sys.disk}")
        lines.append(f"  Uptime: {sys.uptime}")
        lines.append(f"  Nginx:  {sys.nginx_status}")

        # Apps
        lines.append("\n── Apps ────────────────────────────────────")
        if not apps:
            lines.append("  No apps deployed.")
        else:
            for app in apps:
                icon = "✓" if app.status == "online" and not app.issues else "✗"
                lines.append(f"\n  {icon} {app.name}")
                lines.append(f"    Status:   {app.status}")
                lines.append(f"    Port:     {app.port or 'unknown'}")
                lines.append(f"    CPU:      {app.cpu}")
                lines.append(f"    Memory:   {app.memory}")
                lines.append(f"    Restarts: {app.restarts}")
                lines.append(f"    HTTP:     {app.http_code} ({'ok' if app.http_ok else 'FAIL'})")
                if app.auto_fixed:
                    lines.append(f"    ⚡ Auto-restarted")
                if app.issues:
                    for issue in app.issues:
                        lines.append(f"    ⚠ {issue}")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
