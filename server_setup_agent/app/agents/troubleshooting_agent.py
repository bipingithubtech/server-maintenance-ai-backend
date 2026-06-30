"""
TroubleshootingAgent — Diagnose and auto-fix common server/app problems.

Problems it handles:
  diagnose_502(app_name)     — 502 Bad Gateway: checks app, port, nginx config
  diagnose_app_crash(name)   — App keeps crashing: reads logs, finds root cause
  diagnose_high_cpu()        — High CPU: finds which process, suggests fix
  diagnose_high_ram()        — High RAM: finds memory hogs, suggests fix
  diagnose_disk_full()       — Disk full: finds largest dirs, clears safe space
  diagnose_nginx()           — Nginx issues: config test, reload, check sites
  diagnose_port_conflict(p)  — Port in use: finds what's using it
  diagnose_db_connection()   — DB not reachable: checks postgres/mysql/redis
  diagnose(problem)          — Natural language: auto-routes to right diagnosis
  full_diagnosis()           — Runs all checks, returns full report

All findings sent to MS Teams.
"""

import re
import json
from typing import Dict, Any, Optional, List
from loguru import logger

from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.nginx_tool import NginxTool
from app.tools.pm2_tool import PM2Tool
from app.services.teams_alert_service import TeamsAlerter


class TroubleshootingAgent:

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
        self.nginx        = NginxTool(self.executor)
        self.pm2          = PM2Tool(self.executor)
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")

    # ── helpers ────────────────────────────────────────────────────────────────

    def _exec(self, cmd: str):
        _, out, err = self.executor.execute(cmd)
        return out.strip(), err.strip()

    def _run(self, cmd: str) -> str:
        logger.info(f"  [RUN] {cmd[:160]}")
        return self.linux.run_custom_command(cmd)

    def _section(self, title: str, lines: List[str]) -> str:
        out = [f"\n── {title} {'─' * max(0, 44 - len(title))}"]
        out.extend(f"  {l}" for l in lines)
        return "\n".join(out)

    # ── 1. 502 Bad Gateway ─────────────────────────────────────────────────────

    def diagnose_502(self, app_name: str) -> str:
        """
        Diagnoses 502 Bad Gateway for an app.
        Checks: app running? right port? nginx proxying to correct port?
        Auto-fixes: restarts stopped app, corrects nginx port mismatch.
        """
        logger.info(f"[TROUBLESHOOT] 502 diagnosis for {app_name}")
        findings = []
        fixes    = []

        # ── Is the app running? ────────────────────────────────────────────
        out, _ = self._exec("pm2 jlist 2>/dev/null")
        app_status = "not_found"
        app_port   = None
        try:
            apps = json.loads(out)
            for app in apps:
                if app.get("name") == app_name:
                    app_status = app.get("pm2_env", {}).get("status", "unknown")
                    break
        except Exception:
            pass

        findings.append(f"PM2 status: {app_status}")

        if app_status in ("stopped", "errored", "not_found"):
            findings.append(f"⚠ App is {app_status} — not running")
            try:
                self.executor.execute(f"pm2 restart {app_name} 2>/dev/null")
                fixes.append(f"✓ Auto-restarted PM2 app '{app_name}'")
            except Exception as e:
                fixes.append(f"✗ Auto-restart failed: {e}")
        else:
            findings.append("✓ App is running in PM2")

        # ── What port is the app listening on? ────────────────────────────
        out, _ = self._exec("ss -tlnp | grep node")
        port_match = re.search(r':(\d{3,5})\s', out)
        actual_port = port_match.group(1) if port_match else None
        findings.append(f"App listening on port: {actual_port or 'NOT FOUND'}")

        if not actual_port:
            # Try .env
            env_out, _ = self._exec(
                f"grep -i '^PORT=' /opt/{app_name}/.env 2>/dev/null | head -1"
            )
            if env_out:
                actual_port = env_out.split("=", 1)[-1].strip().strip('"')
                findings.append(f"Port from .env: {actual_port}")

        # ── What port is nginx proxying to? ───────────────────────────────
        nginx_conf = f"/etc/nginx/sites-available/{app_name}.conf"
        conf_out, _ = self._exec(f"cat {nginx_conf} 2>/dev/null")
        nginx_port = None
        if conf_out:
            m = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+)', conf_out)
            if m:
                nginx_port = m.group(1)
        findings.append(f"Nginx proxying to port: {nginx_port or 'NOT FOUND'}")

        # ── Port mismatch fix ──────────────────────────────────────────────
        if actual_port and nginx_port and actual_port != nginx_port:
            findings.append(f"⚠ PORT MISMATCH: app={actual_port}, nginx={nginx_port}")
            fix_cmd = (
                f"sudo sed -i 's/127\\.0\\.0\\.1:{nginx_port}/127.0.0.1:{actual_port}/' "
                f"{nginx_conf}"
            )
            self.executor.execute(fix_cmd)
            self.executor.execute("sudo nginx -t 2>/dev/null && sudo systemctl reload nginx")
            fixes.append(f"✓ Fixed nginx port: {nginx_port} → {actual_port}")
        elif actual_port and nginx_port and actual_port == nginx_port:
            findings.append("✓ Port matches — nginx config is correct")

        # ── Nginx running? ─────────────────────────────────────────────────
        nginx_status, _ = self._exec("systemctl is-active nginx")
        findings.append(f"Nginx status: {nginx_status}")
        if nginx_status != "active":
            self.executor.execute("sudo systemctl start nginx")
            fixes.append("✓ Started nginx")

        # ── Nginx config test ──────────────────────────────────────────────
        _, nginx_test_err = self._exec("sudo nginx -t 2>&1")
        if "successful" in nginx_test_err:
            findings.append("✓ Nginx config syntax OK")
        else:
            findings.append(f"⚠ Nginx config error: {nginx_test_err[:200]}")

        result = (
            self._section("Findings", findings) +
            (self._section("Auto-fixes applied", fixes) if fixes else "")
        )

        if fixes:
            self.alerter.info(
                title=f"502 auto-fixed for {app_name}",
                server=self.server_label,
                details="\n".join(fixes),
            )
        else:
            self.alerter.warning(
                title=f"502 on {app_name} — manual review needed",
                server=self.server_label,
                details="\n".join(f for f in findings if "⚠" in f),
            )

        return result

    # ── 2. App crash diagnosis ─────────────────────────────────────────────────

    def diagnose_app_crash(self, app_name: str) -> str:
        """
        Reads PM2 / systemd error logs and identifies the root cause of crashes.
        Common causes: missing .env, wrong entry file, port conflict, OOM.
        """
        logger.info(f"[TROUBLESHOOT] App crash diagnosis: {app_name}")
        findings = []

        # PM2 error logs
        out, _ = self._exec(
            f"pm2 logs {app_name} --lines 30 --nostream --no-color 2>/dev/null"
        )
        error_lines = [l for l in out.splitlines() if "error" in l.lower() or "Error" in l]
        findings.append(f"Last {len(error_lines)} error lines from PM2 logs:")
        findings.extend(error_lines[-10:] if error_lines else ["  (no errors found)"])

        # Common error patterns
        causes = []
        full_log = out.lower()
        if "cannot find module" in full_log or "module not found" in full_log:
            causes.append("Missing Node module — run: npm install --prefix /opt/" + app_name)
        if "eaddrinuse" in full_log or "address already in use" in full_log:
            causes.append("Port already in use — check with: ss -tlnp")
        if "env" in full_log and ("undefined" in full_log or "not set" in full_log):
            causes.append("Missing environment variable — check .env file")
        if "out of memory" in full_log or "heap out of memory" in full_log:
            causes.append("Out of memory — server needs more RAM or memory limit raised")
        if "permission denied" in full_log:
            causes.append("Permission error — check file ownership: ls -la /opt/" + app_name)
        if "syntax error" in full_log:
            causes.append("Syntax error in application code — check recent git changes")

        # Systemd logs (for Python apps)
        svc_out, _ = self._exec(
            f"sudo journalctl -u {app_name} -n 20 --no-pager 2>/dev/null"
        )
        if svc_out and "active" not in svc_out:
            svc_errors = [l for l in svc_out.splitlines() if "error" in l.lower()]
            if svc_errors:
                findings.append("\nSystemd errors:")
                findings.extend(svc_errors[-5:])
                if "modulenotfounderror" in svc_out.lower():
                    causes.append("Python module missing — run: pip install -r requirements.txt")

        # Restart count
        out2, _ = self._exec("pm2 jlist 2>/dev/null")
        try:
            apps = json.loads(out2)
            for app in apps:
                if app.get("name") == app_name:
                    restarts = app.get("pm2_env", {}).get("restart_time", 0)
                    findings.append(f"\nRestart count: {restarts}")
        except Exception:
            pass

        if causes:
            findings.append("\nLikely causes:")
            for c in causes:
                findings.append(f"  → {c}")

        return self._section(f"Crash Diagnosis: {app_name}", findings)

    # ── 3. High CPU ────────────────────────────────────────────────────────────

    def diagnose_high_cpu(self) -> str:
        """Finds top CPU-consuming processes and suggests fixes."""
        logger.info("[TROUBLESHOOT] High CPU diagnosis")
        findings = []

        out, _ = self._exec("ps aux --sort=-%cpu | head -10")
        findings.append("Top CPU processes:")
        for line in out.splitlines()[1:6]:
            findings.append(f"  {line[:100]}")

        # Overall CPU
        out2, _ = self._exec("top -bn1 | grep 'Cpu(s)'")
        findings.append(f"\nCPU summary: {out2}")

        # Common fixes
        findings.append("\nCommon fixes:")
        findings.append("  → If node/python app: check for infinite loops in recent code changes")
        findings.append("  → If npm run build: wait — builds are CPU-intensive but temporary")
        findings.append("  → If unknown process: sudo kill -9 <PID>")

        return self._section("High CPU Diagnosis", findings)

    # ── 4. High RAM ────────────────────────────────────────────────────────────

    def diagnose_high_ram(self) -> str:
        """Finds top memory-consuming processes."""
        logger.info("[TROUBLESHOOT] High RAM diagnosis")
        findings = []

        out, _ = self._exec("ps aux --sort=-%mem | head -10")
        findings.append("Top memory processes:")
        for line in out.splitlines()[1:6]:
            findings.append(f"  {line[:100]}")

        out2, _ = self._exec("free -m")
        findings.append(f"\nMemory summary:\n{out2}")

        findings.append("\nCommon fixes:")
        findings.append("  → Restart memory-leaking app: pm2 restart <app>")
        findings.append("  → Clear page cache: sudo sync && sudo sysctl -w vm.drop_caches=3")
        findings.append("  → Add swap if no swap exists: check with 'free -m'")

        return self._section("High RAM Diagnosis", findings)

    # ── 5. Disk full ───────────────────────────────────────────────────────────

    def diagnose_disk_full(self) -> str:
        """Finds what's consuming disk space and clears safe items."""
        logger.info("[TROUBLESHOOT] Disk full diagnosis")
        findings = []
        fixes    = []

        out, _ = self._exec("df -h /")
        findings.append(f"Disk usage:\n{out}")

        # Top 10 largest dirs under /opt and /var
        out2, _ = self._exec("du -sh /opt/* 2>/dev/null | sort -rh | head -5")
        findings.append(f"\nLargest /opt dirs:\n{out2}")

        out3, _ = self._exec("du -sh /var/log/* 2>/dev/null | sort -rh | head -5")
        findings.append(f"\nLargest /var/log entries:\n{out3}")

        # Auto-clean safe items
        safe_cleanups = [
            ("npm cache",  "npm cache clean --force 2>/dev/null"),
            ("apt cache",  "sudo apt-get clean -y 2>/dev/null"),
            ("old journal","sudo journalctl --vacuum-size=100M 2>/dev/null"),
            ("tmp files",  "sudo find /tmp -type f -atime +7 -delete 2>/dev/null"),
        ]
        for label, cmd in safe_cleanups:
            self.executor.execute(cmd)
            fixes.append(f"✓ Cleared {label}")

        # Check docker
        code, _, _ = self.executor.execute("which docker 2>/dev/null")
        if code == 0:
            self.executor.execute("docker system prune -f 2>/dev/null")
            fixes.append("✓ Docker: pruned unused images/containers")

        # Re-check disk
        out_after, _ = self._exec("df -h / | awk 'NR==2{print $5}'")
        fixes.append(f"Disk usage now: {out_after}")

        return (
            self._section("Disk Full Diagnosis", findings) +
            self._section("Auto-cleanup applied", fixes)
        )

    # ── 6. Nginx diagnosis ─────────────────────────────────────────────────────

    def diagnose_nginx(self) -> str:
        """Tests nginx config, checks enabled sites, reloads if needed."""
        logger.info("[TROUBLESHOOT] Nginx diagnosis")
        findings = []
        fixes    = []

        # Status
        status, _ = self._exec("systemctl is-active nginx")
        findings.append(f"Nginx status: {status}")

        # Config test
        _, test_out = self._exec("sudo nginx -t 2>&1")
        if "successful" in test_out:
            findings.append("✓ Config syntax OK")
        else:
            findings.append(f"⚠ Config error:\n{test_out}")

        # Enabled sites
        out, _ = self._exec("ls /etc/nginx/sites-enabled/ 2>/dev/null")
        findings.append(f"\nEnabled sites:\n{out or '(none)'}")

        # Recent error log
        out, _ = self._exec("sudo tail -20 /var/log/nginx/error.log 2>/dev/null")
        if out:
            findings.append(f"\nRecent nginx errors:\n{out[-500:]}")

        # Auto-fix: reload if running
        if status == "active" and "successful" in test_out:
            self.executor.execute("sudo systemctl reload nginx")
            fixes.append("✓ Nginx reloaded")
        elif status != "active":
            self.executor.execute("sudo systemctl start nginx")
            fixes.append("✓ Nginx started")

        return (
            self._section("Nginx Diagnosis", findings) +
            (self._section("Auto-fixes", fixes) if fixes else "")
        )

    # ── 7. Port conflict ───────────────────────────────────────────────────────

    def diagnose_port_conflict(self, port: str) -> str:
        """Identifies what process is using a given port."""
        logger.info(f"[TROUBLESHOOT] Port conflict: {port}")
        findings = []

        out, _ = self._exec(f"ss -tlnp | grep :{port}")
        if not out:
            return f"Port {port} is free — nothing is using it."

        findings.append(f"Port {port} is in use:")
        findings.append(out)

        # Get PID and process name
        pid_match = re.search(r'pid=(\d+)', out)
        if pid_match:
            pid = pid_match.group(1)
            pname, _ = self._exec(f"ps -p {pid} -o comm= 2>/dev/null")
            cmd_line, _ = self._exec(f"ps -p {pid} -o cmd= 2>/dev/null")
            findings.append(f"\nProcess: {pname} (PID {pid})")
            findings.append(f"Command: {cmd_line[:200]}")
            findings.append(f"\nTo free the port: sudo kill {pid}")
            findings.append(f"Or force kill:     sudo kill -9 {pid}")

        return self._section(f"Port {port} Conflict", findings)

    # ── 8. DB connection ───────────────────────────────────────────────────────

    def diagnose_db_connection(self) -> str:
        """Checks if common databases (postgres, mysql, redis) are running."""
        logger.info("[TROUBLESHOOT] DB connection diagnosis")
        findings = []

        db_checks = [
            ("PostgreSQL", "systemctl is-active postgresql 2>/dev/null", "5432"),
            ("MySQL",      "systemctl is-active mysql 2>/dev/null",      "3306"),
            ("Redis",      "systemctl is-active redis 2>/dev/null",      "6379"),
            ("MongoDB",    "systemctl is-active mongod 2>/dev/null",     "27017"),
        ]

        for name, cmd, port in db_checks:
            status, _ = self._exec(cmd)
            port_out, _ = self._exec(f"ss -tlnp | grep :{port}")
            if status == "active":
                findings.append(f"✓ {name}: active (port {port})")
            elif port_out:
                findings.append(f"✓ {name}: listening on port {port}")
            else:
                findings.append(f"✗ {name}: not running")

        # Check for connection errors in app logs
        out, _ = self._exec(
            "pm2 logs --lines 50 --nostream --no-color 2>/dev/null | "
            "grep -i 'connection\\|ECONNREFUSED\\|timeout\\|database' | tail -10"
        )
        if out:
            findings.append(f"\nDB-related errors in app logs:\n{out}")

        return self._section("Database Connection Diagnosis", findings)

    # ── 9. Natural language router ─────────────────────────────────────────────

    def diagnose(self, problem: str) -> str:
        """
        Takes a natural language problem description and routes to the right diagnosis.
        Examples:
          "502 bad gateway on ats-backend"
          "app keeps crashing"
          "disk is full"
          "high cpu usage"
          "nginx not working"
          "port 3000 in use"
          "database not connecting"
        """
        p = problem.lower()

        # Extract app name if mentioned
        app_match = re.search(r'\b([\w\-]+(?:frontend|backend|api|app|service))\b', p)
        app_name  = app_match.group(1) if app_match else "unknown"

        # Extract port if mentioned
        port_match = re.search(r'port\s*(\d{3,5})|(\d{3,5})\s*(?:is\s+)?in\s+use', p)
        port = (port_match.group(1) or port_match.group(2)) if port_match else None

        if "502" in p or "bad gateway" in p:
            return self.diagnose_502(app_name)
        elif "crash" in p or "restart" in p or "stopped" in p or "errored" in p:
            return self.diagnose_app_crash(app_name)
        elif "cpu" in p:
            return self.diagnose_high_cpu()
        elif "ram" in p or "memory" in p or "oom" in p:
            return self.diagnose_high_ram()
        elif "disk" in p or "storage" in p or "space" in p or "full" in p:
            return self.diagnose_disk_full()
        elif "nginx" in p or "reverse proxy" in p:
            return self.diagnose_nginx()
        elif "port" in p and port:
            return self.diagnose_port_conflict(port)
        elif "database" in p or "db" in p or "redis" in p or "postgres" in p or "mysql" in p:
            return self.diagnose_db_connection()
        else:
            # Run full diagnosis if unsure
            return self.full_diagnosis()

    # ── 10. Full diagnosis ─────────────────────────────────────────────────────

    def full_diagnosis(self) -> str:
        """Runs all checks and returns a comprehensive report."""
        logger.info("[TROUBLESHOOT] Running full diagnosis")
        report = ["=" * 55, "  FULL SYSTEM DIAGNOSIS REPORT", "=" * 55]

        checks = [
            ("Nginx",        self.diagnose_nginx),
            ("CPU",          self.diagnose_high_cpu),
            ("RAM",          self.diagnose_high_ram),
            ("Disk",         self.diagnose_disk_full),
            ("Database",     self.diagnose_db_connection),
        ]

        for label, fn in checks:
            try:
                result = fn()
                report.append(result)
            except Exception as e:
                report.append(f"\n── {label} check failed: {e}")

        report.append("\n" + "=" * 55)
        return "\n".join(report)
