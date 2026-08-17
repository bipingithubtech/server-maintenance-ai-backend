"""
MaintenanceAgent — Routine server and app maintenance.

Tasks:
  execute_task(query)      — LLM intent router: maps free-text → right method
  update_app(app_name)     — git pull → rebuild → restart (PM2/systemd/docker)
  rotate_logs(app_name)    — truncate large logs, flush PM2 logs
  clear_disk()             — remove npm/pip/docker cache, free disk space
  cleanup_scan()           — identify old logs, temp files, unused backups;
                             confirm with user before removing anything
  restart_service(name)    — restart PM2 app / systemd service / docker container
  system_update()          — apt-get update + upgrade
  full_maintenance()       — runs all tasks in sequence

All results reported to MS Teams.
"""

import json
import re
from typing import Dict, Any, Optional, List
from loguru import logger

from langchain_core.messages import SystemMessage, HumanMessage

from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.package_tool import PackageTool
from app.tools.pm2_tool import PM2Tool
from app.tools.systemd_tool import SystemdTool
from app.services.teams_alert_service import TeamsAlerter
from app.services.conversation_service import NeedsInputError
from app.services.llm_service import get_llm


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

        # ── Non-docker: rebuild ────────────────────────────────────────────
        stack = self._detect_stack(app_path)
        try:
            if stack in ("nodejs", "nextjs", "nestjs"):
                self._run(f"npm install --prefix {app_path}")
                if stack in ("nextjs", "nestjs"):
                    self.executor.execute(f"rm -rf {app_path}/.next {app_path}/dist 2>/dev/null")
                    self._run(f"npm run build --prefix {app_path}")
                results.append(f"✓ rebuild: npm ({stack})")

            elif stack in ("fastapi", "flask", "django", "python"):
                self._run(f"{app_path}/venv/bin/pip install -r {app_path}/requirements.txt")
                results.append("✓ rebuild: pip install")

            else:
                results.append(f"⚠ rebuild: unknown stack '{stack}', skipped")

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
            self.restart_service(app_name, pm)
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

    # ── Task 4: Cleanup scan ──────────────────────────────────────────────────

    def cleanup_scan(self, confirmed_paths: Optional[List[str]] = None) -> str:
        """
        Two-phase safe cleanup:

        Phase 1 (scan): Finds cleanup candidates across these categories:
          - Rotated / compressed logs  (/var/log/**/*.gz, *.1, *.2 etc.)
          - Orphaned temp files        (/tmp files older than 7 days)
          - Unused backup files        (*.bak, *.bak.*, *.orig, *.old outside /opt apps)
          - Large log files            (/var/log files > 100 MB)
          - PM2 log archives           (~/.pm2/logs/*.gz)

        Phase 2 (remove): Only runs if caller passes confirmed_paths.
          Deletes exactly those paths, one by one, logging each.
          Returns a before/after disk usage summary.
        """
        logger.info("[MAINTENANCE] Running cleanup scan")

        # ── Phase 2: user confirmed — delete the agreed list ──────────────
        if confirmed_paths is not None:
            return self._cleanup_remove(confirmed_paths)

        # ── Phase 1: scan and collect candidates ──────────────────────────
        candidates: List[Dict] = []  # {category, path, size_mb}

        def _collect(category: str, cmd: str) -> None:
            """Run a find/stat command and parse 'SIZE PATH' lines."""
            out, _ = self._exec(cmd)
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    size_bytes = int(parts[0])
                    path = parts[1].strip()
                    candidates.append({
                        "category": category,
                        "path": path,
                        "size_mb": round(size_bytes / 1024 / 1024, 2),
                    })
                except ValueError:
                    continue

        # Rotated / compressed log files
        _collect(
            "rotated log",
            "find /var/log -type f \\( -name '*.gz' -o -name '*.1' -o -name '*.2' "
            "-o -name '*.3' -o -name '*.4' -o -name '*.old' \\) "
            "-printf '%s %p\\n' 2>/dev/null",
        )

        # Large active log files (> 100 MB)
        _collect(
            "large log (>100MB)",
            "find /var/log -type f -size +100M -printf '%s %p\\n' 2>/dev/null",
        )

        # Old temp files (not accessed in 7+ days)
        _collect(
            "old temp file (7d+)",
            "find /tmp -type f -atime +7 -printf '%s %p\\n' 2>/dev/null",
        )

        # Backup files outside /opt app dirs (*.bak, *.bak.NNN, *.orig, *.old)
        _collect(
            "unused backup",
            "find /etc /home /root -maxdepth 5 -type f "
            "\\( -name '*.bak' -o -name '*.bak.*' -o -name '*.orig' -o -name '*.old' \\) "
            "-printf '%s %p\\n' 2>/dev/null",
        )

        # OpsAgent backup files from file edits (*.bak.<timestamp> on config files)
        _collect(
            "ops backup",
            "find /etc -type f -name '*.bak.[0-9]*' -printf '%s %p\\n' 2>/dev/null",
        )

        # PM2 archived logs
        pm2_log_dir_out, _ = self._exec("echo ~/.pm2/logs")
        pm2_log_dir = pm2_log_dir_out.strip()
        if pm2_log_dir:
            _collect(
                "PM2 log archive",
                f"find {pm2_log_dir} -type f -name '*.gz' -printf '%s %p\\n' 2>/dev/null",
            )

        # ── Nothing found ──────────────────────────────────────────────────
        if not candidates:
            return "✅ Cleanup scan complete — no cleanup candidates found. Server looks clean."

        # ── Build human-readable report ────────────────────────────────────
        total_mb = sum(c["size_mb"] for c in candidates)

        # Group by category
        by_category: Dict[str, List[Dict]] = {}
        for c in candidates:
            by_category.setdefault(c["category"], []).append(c)

        lines = [
            f"🔍 Cleanup scan found **{len(candidates)} candidate(s)** "
            f"totalling ~{total_mb:.1f} MB:\n"
        ]
        for cat, items in by_category.items():
            cat_mb = sum(i["size_mb"] for i in items)
            lines.append(f"**{cat}** ({len(items)} file(s), ~{cat_mb:.1f} MB)")
            for item in items[:10]:  # cap per-category preview at 10 lines
                lines.append(f"  • {item['path']}  ({item['size_mb']} MB)")
            if len(items) > 10:
                lines.append(f"  … and {len(items) - 10} more")
            lines.append("")

        # Serialize paths list so the resume handler can pass it back
        path_list = json.dumps([c["path"] for c in candidates])
        lines.append(
            f"⚠️  Reply **yes** to delete all {len(candidates)} file(s) "
            f"and recover ~{total_mb:.1f} MB, or **no** to cancel."
        )

        raise NeedsInputError(
            "\n".join(lines),
            {
                "step": "cleanup_confirm",
                "agent": "maintenance",
                "pending_paths": path_list,   # JSON-encoded list for safe transport
            },
        )

    def _cleanup_remove(self, paths: List[str]) -> str:
        """Delete a pre-approved list of files and report results."""
        logger.info(f"[MAINTENANCE] Removing {len(paths)} confirmed cleanup file(s)")
        removed, failed = [], []

        for path in paths:
            # Safety guardrail: never delete anything outside known safe prefixes
            safe_prefixes = ("/var/log/", "/tmp/", "/etc/", "/home/", "/root/",
                             "/opt/", "/root/.pm2/logs/")
            if not any(path.startswith(p) for p in safe_prefixes):
                failed.append(f"⛔ SKIPPED (unsafe path): {path}")
                continue

            code, _, err = self.executor.execute(f"sudo rm -f {path} 2>&1")
            if code == 0:
                removed.append(f"✓ removed: {path}")
                logger.info(f"  [CLEANUP] removed {path}")
            else:
                failed.append(f"✗ failed: {path}  ({err.strip()})")
                logger.warning(f"  [CLEANUP] failed to remove {path}: {err.strip()}")

        # Report disk delta
        disk_after = self._get_disk_free_pct()
        lines = [
            f"🧹 Cleanup complete — {len(removed)} removed, {len(failed)} failed.",
            f"Disk usage now: {disk_after:.0f}%",
            "",
        ] + removed + ([""] + failed if failed else [])

        summary = "\n".join(lines)
        self.alerter.info(
            title="Cleanup scan completed",
            server=self.server_label,
            details=f"{len(removed)} files removed, {len(failed)} failed.",
        )
        return summary

    # ── Task 5: Restart service ────────────────────────────────────────────────

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

    # ── Task 6: System update ─────────────────────────────────────────────────

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

    # ── Task 7: execute_task — LLM intent router ─────────────────────────────

    # System prompt used to classify the user's intent into one structured action
    _INTENT_PROMPT = """You are a maintenance dispatcher for a Linux server.
Parse the user's request and return a single JSON object with:
  "action"   : one of update_app | rotate_logs | clear_disk | cleanup_scan |
                restart_service | system_update | full_maintenance
  "app_name" : string (only for update_app, rotate_logs, restart_service — null otherwise)
  "reason"   : one sentence explaining your choice

Rules:
- update_app      → user wants to pull latest code / redeploy a named app
- rotate_logs     → user wants to flush, rotate, or trim logs
- clear_disk      → user wants to free disk space via cache purge (NOT file deletion)
- cleanup_scan    → user wants to find and remove old logs, temp files, unused backups
- restart_service → user wants to restart a named service/app
- system_update   → user wants apt-get update/upgrade or OS package updates
- full_maintenance → user wants "full maintenance", "everything", or "all tasks"

If the user names a specific app/service, set app_name to that name exactly.
If no app is named, set app_name to null.

Return ONLY valid JSON. No markdown, no explanation.

Examples:
User: "update my-api"
→ {"action":"update_app","app_name":"my-api","reason":"User wants to update the my-api app."}

User: "restart nginx"
→ {"action":"restart_service","app_name":"nginx","reason":"User wants to restart nginx."}

User: "free up some disk space"
→ {"action":"clear_disk","app_name":null,"reason":"User wants to purge caches to free disk."}

User: "remove old logs and temp files"
→ {"action":"cleanup_scan","app_name":null,"reason":"User wants to scan and remove old files."}

User: "run full maintenance"
→ {"action":"full_maintenance","app_name":null,"reason":"User wants all maintenance tasks run."}
"""

    def execute_task(self, query: str) -> str:
        """
        LLM-powered intent router.

        Classifies the user's free-text query into one of the concrete
        maintenance actions, then calls the right method directly.
        Falls back to a safe "unknown intent" message if classification fails.
        """
        llm = get_llm()

        # ── Classify intent ────────────────────────────────────────────────
        try:
            response = llm.invoke([
                SystemMessage(content=self._INTENT_PROMPT),
                HumanMessage(content=query),
            ])
            raw = response.content.strip()
            # Strip markdown fences if the LLM wraps output
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            intent = json.loads(raw)
        except Exception as exc:
            logger.warning(f"[MAINTENANCE] Intent classification failed: {exc}")
            intent = {"action": "unknown", "app_name": None}

        action   = intent.get("action", "unknown")
        app_name = intent.get("app_name") or None
        reason   = intent.get("reason", "")
        logger.info(f"[MAINTENANCE] Intent → action={action!r} app={app_name!r}  ({reason})")

        # ── Dispatch ────────────────────────────────────────────────────────
        if action == "update_app":
            if not app_name:
                # Try to discover all PM2 apps and update them all
                out, _ = self._exec("pm2 jlist 2>/dev/null")
                try:
                    apps = json.loads(out)
                    names = [a.get("name") for a in apps if a.get("name")]
                except Exception:
                    names = []
                if not names:
                    return (
                        "I couldn't find any running apps to update. "
                        "Please specify the app name, e.g. 'update my-app'."
                    )
                results = []
                for name in names:
                    results.append(f"── {name} ──────────")
                    results.append(self.update_app(name))
                return "\n".join(results)
            return self.update_app(app_name)

        elif action == "rotate_logs":
            return self.rotate_logs(app_name)

        elif action == "clear_disk":
            return self.clear_disk()

        elif action == "cleanup_scan":
            return self.cleanup_scan()

        elif action == "restart_service":
            if not app_name:
                return (
                    "Please tell me which service to restart, "
                    "e.g. 'restart nginx' or 'restart my-app'."
                )
            return self.restart_service(app_name)

        elif action == "system_update":
            return self.system_update()

        elif action == "full_maintenance":
            return self.full_maintenance()

        else:
            return (
                f"I'm not sure what maintenance task you need. I can help with:\n"
                f"• **update app** — git pull + rebuild + restart a deployed app\n"
                f"• **rotate logs** — flush PM2 / nginx / journalctl logs\n"
                f"• **clear disk** — purge npm, pip, docker, apt caches\n"
                f"• **cleanup scan** — find and safely remove old logs, temp files, backups\n"
                f"• **restart service** — restart any PM2 app, systemd service, or container\n"
                f"• **system update** — apt-get update + upgrade\n"
                f"• **full maintenance** — run all of the above\n\n"
                f"What would you like to do?"
            )

    # ── Task 8: Full maintenance ───────────────────────────────────────────────

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
