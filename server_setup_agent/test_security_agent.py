"""
Security Agent — Full test suite covering every security scenario.

Test cases:
  1. Open ports audit          — detects unexpected listening ports
  2. SSH brute force detection — detects failed login attempts
  3. Sudo user audit           — lists privileged users
  4. UFW firewall setup        — blocks all except allowed ports
  5. Fail2ban setup            — brute-force protection
  6. SSH hardening             — disables root login
  7. Full audit                — runs all read-only checks + Teams alerts
  8. Full harden               — applies all protections (run once)
"""

import paramiko
import time
from app.agents.security_agent import SecurityAgent

SSH_CONFIG = {
    "host": "192.168.56.101",
    "username": "bipin123",
    "password": "bipin",
    "port": 22,
}

agent = SecurityAgent(
    executor_type="ssh",
    executor_config=SSH_CONFIG,
    server_label="aiagent-192.168.56.101",
)


def section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  TEST: {title}")
    print('=' * 60)


def run_on_server(cmd: str) -> str:
    """Run a command directly on the VM for test setup."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=SSH_CONFIG["host"],
        username=SSH_CONFIG["username"],
        password=SSH_CONFIG["password"],
        port=SSH_CONFIG["port"],
    )
    _, out, err = client.exec_command(cmd)
    result = out.read().decode() + err.read().decode()
    client.close()
    return result.strip()


# ── TEST 1: Open ports audit ──────────────────────────────────────────────────
section("1 — Open Ports Audit (detects unexpected ports)")

# Simulate: open a dummy listener on an unexpected port
print("  [SETUP] Opening dummy listener on port 9999...")
run_on_server("nohup nc -lk 9999 &>/dev/null & echo $!")
time.sleep(1)

print("\n  [RUN] check_open_ports()")
result = agent.check_open_ports()
print(result)
print("\n  [EXPECT] Port 9999 flagged as unexpected + Teams 🔵 SECURITY alert sent")

# Cleanup
run_on_server("pkill -f 'nc -lk 9999' 2>/dev/null || true")


# ── TEST 2: SSH brute force detection ─────────────────────────────────────────
section("2 — SSH Brute Force Detection")

# Simulate: inject fake failed SSH attempts into auth.log
print("  [SETUP] Injecting 15 fake failed SSH login entries into auth.log...")
for i in range(15):
    run_on_server(
        f"echo 'Jun 30 10:0{i}:00 aiagent sshd[9999]: Failed password for root "
        f"from 1.2.3.{i} port 5000{i} ssh2' | sudo tee -a /var/log/auth.log > /dev/null"
    )

print("\n  [RUN] check_ssh_bruteforce()")
result = agent.check_ssh_bruteforce()
print(result)
print("\n  [EXPECT] >10 failures detected + top IPs shown + Teams 🔵 SECURITY alert sent")


# ── TEST 3: Sudo user audit ───────────────────────────────────────────────────
section("3 — Sudo User Audit")
print("  [RUN] check_users()")
result = agent.check_users()
print(result)
print("\n  [EXPECT] Lists all sudo users. If multiple UID-0 users → Teams alert")


# ── TEST 4: UFW Firewall setup ────────────────────────────────────────────────
section("4 — UFW Firewall Setup (allows only required ports)")

# Check UFW status before
ufw_before = run_on_server("sudo ufw status 2>/dev/null")
print(f"  UFW before: {ufw_before[:100]}")

print("\n  [RUN] setup_firewall(['4001', '5006', '8001'])")
result = agent.setup_firewall(["4001", "5006", "8001"])
print(result)

ufw_after = run_on_server("sudo ufw status verbose 2>/dev/null")
print(f"\n  UFW after:\n{ufw_after[:400]}")
print("\n  [EXPECT] Only 22/80/443/4001/5006/8001 allowed. All else blocked.")


# ── TEST 5: Fail2ban setup ────────────────────────────────────────────────────
section("5 — Fail2ban Setup (brute-force protection)")
print("  [RUN] setup_fail2ban()")
result = agent.setup_fail2ban()
print(result)

status = run_on_server("sudo fail2ban-client status 2>/dev/null")
print(f"\n  Fail2ban status:\n{status}")
print("\n  [EXPECT] fail2ban installed, sshd jail active, 5 attempts = 10 min ban")


# ── TEST 6: SSH hardening ─────────────────────────────────────────────────────
section("6 — SSH Hardening (disable root login)")

# Check before
root_before = run_on_server("grep PermitRootLogin /etc/ssh/sshd_config 2>/dev/null")
print(f"  PermitRootLogin before: {root_before}")

print("\n  [RUN] disable_root_ssh()")
result = agent.disable_root_ssh()
print(result)

root_after = run_on_server("grep PermitRootLogin /etc/ssh/sshd_config 2>/dev/null")
print(f"\n  PermitRootLogin after: {root_after}")
print("\n  [EXPECT] PermitRootLogin no — root SSH blocked")


# ── TEST 7: Full audit (runs all checks + Teams alerts) ───────────────────────
section("7 — Full Security Audit (read-only, sends Teams alerts)")
print("  [RUN] full_audit()")
result = agent.full_audit()
print(result)
print("\n  [EXPECT] All issues reported to console + Teams 🔵 SECURITY alerts for findings")


# ── TEST 8: Full harden (optional — applies all) ──────────────────────────────
section("8 — Full Harden (UFW + fail2ban + SSH — applies changes)")
confirm = input("\n  Run full harden? This makes permanent changes. (yes/no): ").strip().lower()
if confirm == "yes":
    result = agent.harden(allowed_ports=["4001", "5006", "8001"])
    print(result)
    print("\n  [EXPECT] Teams 🟢 INFO 'Server hardening completed'")
else:
    print("  Skipped.")

print("\n" + "=" * 60)
print("  All security tests complete.")
print("=" * 60)
