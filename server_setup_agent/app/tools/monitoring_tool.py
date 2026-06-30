import re
from app.executors.base_executor import BaseExecutor


class MonitoringTool:
    """Tool for monitoring system resources and application health."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    # ── System resources ───────────────────────────────────────────────────────

    def get_cpu_usage(self) -> str:
        """Gets CPU usage snapshot."""
        exit_code, out, err = self.executor.execute(
            "top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4\"%\"}'"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to get CPU usage:\n{err}")
        return out.strip() or "unknown"

    def get_ram_usage(self) -> str:
        """Gets RAM usage (used/total in MB)."""
        exit_code, out, err = self.executor.execute(
            "free -m | awk 'NR==2{printf \"%sMB used / %sMB total (%.0f%%)\", $3,$2,$3*100/$2}'"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to get RAM usage:\n{err}")
        return out.strip() or "unknown"

    def get_disk_usage(self) -> str:
        """Gets disk usage for root partition."""
        exit_code, out, err = self.executor.execute(
            "df -h / | awk 'NR==2{print $3\" used / \"$2\" total (\"$5\" used)\"}'"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to get disk usage:\n{err}")
        return out.strip() or "unknown"

    def get_system_uptime(self) -> str:
        """Gets server uptime."""
        exit_code, out, err = self.executor.execute("uptime -p")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get uptime:\n{err}")
        return out.strip()

    def get_top_processes(self, count: int = 5) -> str:
        """Gets top N processes by CPU usage."""
        exit_code, out, err = self.executor.execute(
            f"ps aux --sort=-%cpu | head -n {count + 1}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to get top processes:\n{err}")
        return out.strip()

    # ── App health ─────────────────────────────────────────────────────────────

    def get_pm2_status(self) -> str:
        """Gets PM2 process list with status."""
        exit_code, out, err = self.executor.execute("pm2 list --no-color 2>/dev/null")
        if exit_code != 0:
            return "PM2 not running or not installed."
        return out.strip()

    def get_pm2_app_status(self, app_name: str) -> dict:
        """
        Returns status dict for a specific PM2 app using pm2 jlist (JSON).
        Keys: name, status, restarts, cpu, memory
        """
        exit_code, out, err = self.executor.execute("pm2 jlist 2>/dev/null")
        if exit_code == 0 and out.strip():
            try:
                import json as _json
                data = _json.loads(out)
                for app in data:
                    if app.get("name") == app_name:
                        pm2_env = app.get("pm2_env", {})
                        monit   = app.get("monit", {})
                        status  = pm2_env.get("status", "unknown")
                        restarts = pm2_env.get("restart_time", 0)
                        cpu    = f"{monit.get('cpu', 0)}%"
                        mem_b  = monit.get("memory", 0)
                        mem    = f"{round(mem_b / 1024 / 1024, 1)}mb"
                        return {
                            "name":     app_name,
                            "status":   status,
                            "restarts": restarts,
                            "cpu":      cpu,
                            "memory":   mem,
                        }
            except Exception:
                pass

        return {"name": app_name, "status": "not_found", "restarts": 0}

    def get_pm2_logs(self, app_name: str, lines: int = 30) -> str:
        """Gets recent PM2 logs for an app."""
        exit_code, out, err = self.executor.execute(
            f"pm2 logs {app_name} --lines {lines} --nostream --no-color 2>/dev/null"
        )
        return out.strip() or err.strip()

    def restart_pm2_app(self, app_name: str) -> str:
        """Restarts a PM2 application."""
        exit_code, out, err = self.executor.execute(f"pm2 restart {app_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart {app_name}: {err}")
        return f"PM2 app '{app_name}' restarted."

    def get_nginx_status(self) -> str:
        """Gets nginx service status."""
        exit_code, out, err = self.executor.execute(
            "systemctl is-active nginx 2>/dev/null"
        )
        return out.strip() or "inactive"

    def get_nginx_errors(self, lines: int = 20) -> str:
        """Gets recent nginx error log entries."""
        exit_code, out, err = self.executor.execute(
            f"sudo tail -n {lines} /var/log/nginx/error.log 2>/dev/null"
        )
        return out.strip() or "No nginx error log found."

    def get_app_nginx_errors(self, app_name: str, lines: int = 20) -> str:
        """Gets nginx error log for a specific app."""
        exit_code, out, err = self.executor.execute(
            f"sudo tail -n {lines} /var/log/nginx/{app_name}.error.log 2>/dev/null"
        )
        return out.strip() or f"No error log found for {app_name}."

    def check_port_listening(self, port: str) -> bool:
        """Returns True if something is listening on the given port."""
        exit_code, out, _ = self.executor.execute(f"ss -tlnp | grep :{port}")
        return bool(out.strip())

    def get_listening_ports(self) -> str:
        """Lists all currently listening TCP ports with process names."""
        exit_code, out, err = self.executor.execute("ss -tlnp")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get listening ports:\n{err}")
        return out.strip()

    def check_http_endpoint(self, url: str) -> dict:
        """
        Hits an HTTP endpoint with curl and returns status code + response snippet.
        """
        exit_code, out, err = self.executor.execute(
            f"curl -s -o /tmp/_health_resp -w '%{{http_code}}' --max-time 5 {url}"
        )
        status_code = out.strip()
        _, body, _ = self.executor.execute("cat /tmp/_health_resp 2>/dev/null | head -c 200")
        return {
            "url": url,
            "status_code": status_code,
            "body": body.strip()[:200],
            "reachable": status_code.isdigit() and int(status_code) < 500,
        }
