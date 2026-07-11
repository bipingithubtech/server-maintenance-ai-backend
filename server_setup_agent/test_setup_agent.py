"""
test_setup_agent.py — Manual integration test for SetupAgent.

Run from the server_setup_agent directory:
    python test_setup_agent.py

Flow:
  1. Asks for server credentials (host, username, password)
  2. Connects to the server
  3. Scans what's already installed
  4. Shows current state — what's there, what's missing
  5. LLM parses your request and plans only what's needed
  6. Skips already-installed items, installs only missing ones
"""

import getpass
from app.agents.setup_agent import SetupAgent

# ── Ask for server credentials ────────────────────────────────────────────────
print("─" * 60)
print("  Server Setup Agent")
print("─" * 60)
host     = input("[?] Server host (e.g. 127.0.0.1): ").strip() or "127.0.0.1"
port     = input("[?] SSH port (default 2222):       ").strip() or "2222"
username = input("[?] Username:                       ").strip()
password = getpass.getpass("[?] Password:                       ")
key_file = input("[?] SSH key path (Enter to skip):  ").strip() or None

executor_config = {
    "host":     host,
    "port":     int(port),
    "username": username,
    "password": password,
}
if key_file:
    executor_config["key_filename"] = key_file

# ── Connect and run setup ─────────────────────────────────────────────────────
agent = SetupAgent(
    executor_type="ssh",
    executor_config=executor_config,
    server_label=f"{username}@{host}",
)

query = input("\n[?] What do you want to set up?\n    > ").strip()
if not query:
    query = "setup a fresh server with base packages, firewall, and fail2ban"

result = agent.execute_task(query)

print("\n" + "=" * 60)
print(result)
print("=" * 60)
