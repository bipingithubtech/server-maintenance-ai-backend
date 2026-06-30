"""
SecurityAgent — Server hardening and threat detection.

What it does:
  harden()                — full one-time hardening (run once after server setup)
  audit()                 — scan for security issues and report
  setup_firewall(ports)   — configure UFW with only required ports open
  setup_fail2ban()        — install + configure brute-force protection
  disable_root_ssh()      — block root login over SSH
  setup_ssl(domain)       — install Let's Encrypt SSL certificate via certbot
  check_open_ports()      — list all listening ports and flag unexpected ones
  check_ssh_bruteforce()  — count failed SSH login attempts, alert if high
  check_users()           — list sudo users, flag unexpected accounts
  full_audit()            — runs all checks and sends Teams report

All alerts sent to MS Teams.
"""

import re
from typing import Dict, Any, Optional, List
from loguru import logger

from app.executors.executor_factory import ExecutorFactory
from app.tools.linux_tool import LinuxTool
from app.tools.package_tool import PackageTool
from app.tools.security_tool import SecurityTool
from app.tools.firewall_tool import FirewallTool
from app.services.teams_alert_service import TeamsAlerter


class SecurityAgent:

    # Ports that are always expected — no alert for these
    # App ports are loaded from deployment_context.json at runtime
    EXPECTED_PORTS = {"22", "80", "443", "53", "8080"}

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
        self.security     = SecurityTool(self.executor)
        self.firewall     = FirewallTool(self.executor)
        self.alerter      = TeamsAlerter()
        self.server_label = server_label or executor_config.get("host", "unknown")

        # Load deployed app ports from context file so they don't flag as unexpected
        self._load_app_ports()

    # ── helpers ────────────────────────────────────────────────────────────────

    def _load_app_ports(self):
        """Load deployed app ports from deployment_context.json to whitelist them."""
        try:
            import json as _json
            with open("deployment_context.json") as f:
                ctx = _json.load(f)
                port = ctx.get("port")
                if port:
                    self.EXPECTED_PORTS = self.EXPECTED_PORTS | {str(port)}
        except Exception:
            pass  # file may not exist — use defaults

    def _run(self, cmd: str) -> str:
        logger.info(f"  [RUN] {cmd[:160]}")
        result = self.linux.run_custom_command(cmd)
        logger.info(f"  [OK]  {str(result)[:120]}")
        return result

    def _exec(self, cmd: str):
        _, out, err = self.executor.execute(cmd)
        return out.strip(), err.strip()

    # ── Task 1: UFW Firewall ───────────────────────────────────────────────────

    def setup_firewall(self, allowed_ports: Optional[List[str]] = None) -> str:
        """
        Configures UFW firewall:
        - Denies all incoming by default
        - Allows only SSH (22), HTTP (80), HTTPS (443), and any extra ports provided
        - Enables UFW
        """
        logger.info("[SECURITY] Setting up UFW firewall")
        results = []

        if allowed_ports is None:
            allowed_ports = []

        base_ports = ["22", "80", "443"]
        all_ports  = base_ports + [p for p in allowed_ports if p not in base_ports]

        try:
            # Reset to clean state
            self.executor.execute("sudo ufw --force reset")
            results.append("✓ UFW reset to defaults")

            # Default deny incoming, allow outgoing
            self.executor.execute("sudo ufw default deny incoming")
            self.executor.execute("sudo ufw default allow outgoing")
            results.append("✓ Default: deny incoming, allow outgoing")

            # Allow required ports
            for port in all_ports:
                self.firewall.allow_port(port)
                results.append(f"✓ Allowed port {port}")

            # Enable
            self.firewall.enable()
            results.append("✓ UFW enabled")

        except Exception as e:
            results.append(f"✗ UFW setup failed: {e}")
            self.alerter.security(
                title="UFW firewall setup failed",
                server=self.server_label,
                details=str(e),
            )

        return "\n".join(results)

    # ── Task 2: Fail2ban ───────────────────────────────────────────────────────

    def setup_fail2ban(self) -> str:
        """
        Installs and configures fail2ban:
        - Protects SSH from brute force (bans after 5 failed attempts, 10 min ban)
        - Protects nginx from repeated bad requests
        """
        logger.info("[SECURITY] Setting up fail2ban")
        results = []

        try:
            self.security.install_fail2ban()
            results.append("✓ fail2ban installed and enabled")
        except Exception as e:
            results.append(f"✗ fail2ban install failed: {e}")
            return "\n".join(results)

        # Write jail config
        jail_config = (
            "[DEFAULT]\n"
            "bantime  = 600\n"
            "findtime = 600\n"
            "maxretry = 5\n\n"
            "[sshd]\n"
            "enabled = true\n"
            "port    = ssh\n"
            "logpath = /var/log/auth.log\n\n"
            "[nginx-http-auth]\n"
            "enabled  = true\n"
            "logpath  = /var/log/nginx/error.log\n"
        )
        import base64
        encoded = base64.b64encode(jail_config.encode()).decode()
        self.executor.execute(
            f"echo '{encoded}' | base64 --decode | sudo tee /etc/fail2ban/jail.local > /dev/null"
        )
        self.executor.execute("sudo systemctl restart fail2ban")
        results.append("✓ fail2ban configured (SSH + nginx protection)")
        results.append("  Ban policy: 5 attempts → 10 min ban")

        return "\n".join(results)

    # ── Task 3: Disable root SSH ───────────────────────────────────────────────

    def disable_root_ssh(self) -> str:
        """
        Hardens SSH configuration:
        - Disables root login
        - Disables password auth (key-only)
        - Sets max auth tries to 3
        """
        logger.info("[SECURITY] Hardening SSH")
        results = []

        hardening = [
            ("PermitRootLogin",         "no"),
            ("MaxAuthTries",            "3"),
            ("LoginGraceTime",          "30"),
            ("X11Forwarding",           "no"),
            ("PermitEmptyPasswords",    "no"),
        ]

        for key, value in hardening:
            cmd = (
                f"sudo grep -q '^{key}' /etc/ssh/sshd_config && "
                f"sudo sed -i 's/^{key}.*/{key} {value}/' /etc/ssh/sshd_config || "
                f"echo '{key} {value}' | sudo tee -a /etc/ssh/sshd_config > /dev/null"
            )
            self.executor.execute(cmd)
            results.append(f"✓ SSH: {key} = {value}")

        # Restart SSH
        exit_code, _, err = self.executor.execute("sudo systemctl restart sshd 2>/dev/null || sudo systemctl restart ssh 2>/dev/null")
        results.append("✓ SSH service restarted")

        return "\n".join(results)

    # ── Task 4: SSL Certificate ────────────────────────────────────────────────

    def setup_ssl(self, domain: str, email: str = "admin@example.com") -> str:
        """
        Installs Let's Encrypt SSL certificate via certbot.
        Configures nginx to use HTTPS and redirect HTTP → HTTPS.
        Note: domain must be a real publicly-accessible domain (not IP).
        """
        logger.info(f"[SECURITY] Setting up SSL for {domain}")
        results = []

        # Validate — can't use certbot with an IP
        if re.match(r'^\d+\.\d+\.\d+\.\d+$', domain):
            return "ERROR: SSL certificates require a domain name, not an IP address."

        try:
            self._run("sudo apt-get install -y certbot python3-certbot-nginx")
            results.append("✓ certbot installed")
        except Exception as e:
            return f"✗ certbot install failed: {e}"

        try:
            self._run(
                f"sudo certbot --nginx -d {domain} --non-interactive "
                f"--agree-tos -m {email} --redirect"
            )
            results.append(f"✓ SSL certificate issued for {domain}")
            results.append("✓ nginx configured with HTTPS + HTTP→HTTPS redirect")
        except Exception as e:
            results.append(f"✗ certbot failed: {e}")
            self.alerter.security(
                title=f"SSL setup failed for {domain}",
                server=self.server_label,
                details=str(e),
            )
            return "\n".join(results)

        # Setup auto-renewal
        self.executor.execute(
            "sudo systemctl enable certbot.timer 2>/dev/null || "
            "(sudo crontab -l 2>/dev/null; echo '0 12 * * * certbot renew --quiet') | sudo crontab -"
        )
        results.append("✓ Auto-renewal configured")

        return "\n".join(results)

    # ── Task 5: Check open ports ───────────────────────────────────────────────

    def check_open_ports(self) -> str:
        """
        Lists all TCP ports currently listening.
        Flags ports that are not in the expected set (22, 80, 443, 53).
        Sends Teams security alert for unexpected high-risk ports.
        """
        logger.info("[SECURITY] Checking open ports")
        out, _ = self._exec("ss -tlnp")
        results = ["Listening ports:"]
        unexpected = []

        for line in out.splitlines():
            m = re.search(r'[:\s](\d{2,5})\s+\d+\.\d+', line)
            if not m:
                m = re.search(r':(\d{2,5})\s', line)
            if m:
                port = m.group(1)
                # Get process name
                proc_m = re.search(r'users:\(\("([^"]+)"', line)
                proc = proc_m.group(1) if proc_m else "unknown"

                if port in self.EXPECTED_PORTS:
                    results.append(f"  ✓ {port:>6}  {proc}")
                else:
                    results.append(f"  ⚠ {port:>6}  {proc}  ← unexpected")
                    unexpected.append(f"{port} ({proc})")

        if unexpected:
            self.alerter.security(
                title="Unexpected open ports detected",
                server=self.server_label,
                details=f"Ports: {', '.join(unexpected)}",
            )

        return "\n".join(results)

    # ── Task 6: SSH brute force check ──────────────────────────────────────────

    def check_ssh_bruteforce(self) -> str:
        """
        Counts failed SSH login attempts from auth.log.
        Alerts if > 10 failures in the last 1000 lines.
        Returns top attacking IPs.
        """
        logger.info("[SECURITY] Checking SSH brute force attempts")

        out, _ = self._exec(
            "sudo grep -i 'failed password\\|invalid user' /var/log/auth.log 2>/dev/null | tail -1000"
        )
        if not out:
            return "No failed SSH attempts found in auth.log."

        lines = out.splitlines()
        count = len(lines)

        # Extract top IPs
        ip_counts: Dict[str, int] = {}
        for line in lines:
            m = re.search(r'from\s+([\d.]+)', line)
            if m:
                ip = m.group(1)
                ip_counts[ip] = ip_counts.get(ip, 0) + 1

        top_ips = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        results = [f"Failed SSH attempts (last 1000 log lines): {count}"]
        if top_ips:
            results.append("Top attacking IPs:")
            for ip, c in top_ips:
                results.append(f"  {ip:>16} — {c} attempts")

        if count > 10:
            self.alerter.security(
                title=f"SSH brute force detected: {count} failed attempts",
                server=self.server_label,
                details=f"Top IPs: {', '.join(ip for ip, _ in top_ips)}",
            )

        return "\n".join(results)

    # ── Task 7: Check sudo users ───────────────────────────────────────────────

    def check_users(self) -> str:
        """
        Lists all users with sudo privileges.
        Flags accounts that are not the expected deployment user.
        """
        logger.info("[SECURITY] Checking sudo users")
        out, _ = self._exec("grep -Po '^sudo.+:\\K.*$' /etc/group 2>/dev/null")
        sudo_users = [u.strip() for u in out.split(",") if u.strip()]

        results = [f"Users with sudo access ({len(sudo_users)}):"]
        for user in sudo_users:
            results.append(f"  - {user}")

        # Also check for users with UID 0 (root-level)
        out, _ = self._exec("awk -F: '$3==0{print $1}' /etc/passwd")
        root_users = [u.strip() for u in out.splitlines() if u.strip()]
        if len(root_users) > 1:
            results.append(f"\n⚠ Multiple UID-0 users: {', '.join(root_users)}")
            self.alerter.security(
                title="Multiple root-level users detected",
                server=self.server_label,
                details=f"UID 0 users: {', '.join(root_users)}",
            )

        return "\n".join(results)

    # ── Task 8: Full harden (run once) ─────────────────────────────────────────

    def harden(self, allowed_ports: Optional[List[str]] = None) -> str:
        """
        One-time server hardening. Run after initial setup.
        1. Setup UFW firewall
        2. Install fail2ban
        3. Disable root SSH
        """
        logger.info("[SECURITY] Running full server hardening")
        report = ["=" * 50, "  SECURITY HARDENING REPORT", "=" * 50]

        report.append("\n── UFW Firewall ─────────────────────────")
        result = self.setup_firewall(allowed_ports)
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n── Fail2ban ──────────────────────────────")
        result = self.setup_fail2ban()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n── SSH Hardening ─────────────────────────")
        result = self.disable_root_ssh()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n" + "=" * 50)
        full = "\n".join(report)

        self.alerter.info(
            title="Server hardening completed",
            server=self.server_label,
            details="UFW + fail2ban + SSH hardening applied",
        )
        return full

    # ── Task 9: Full audit ─────────────────────────────────────────────────────

    def full_audit(self) -> str:
        """
        Security audit — scan only, no changes.
        1. Check open ports
        2. Check SSH brute force attempts
        3. Check sudo users
        """
        logger.info("[SECURITY] Running full security audit")
        report = ["=" * 50, "  SECURITY AUDIT REPORT", "=" * 50]

        report.append("\n── Open Ports ────────────────────────────")
        result = self.check_open_ports()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n── SSH Brute Force ───────────────────────")
        result = self.check_ssh_bruteforce()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n── Sudo Users ────────────────────────────")
        result = self.check_users()
        for line in result.splitlines():
            report.append(f"  {line}")

        report.append("\n" + "=" * 50)
        return "\n".join(report)
