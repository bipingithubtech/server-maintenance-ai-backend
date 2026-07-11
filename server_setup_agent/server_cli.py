"""
server_cli.py — Interactive server management CLI.

Connect once, then ask anything about your server in a loop:
  - "what's installed?"
  - "install docker"
  - "show nginx config"
  - "check disk usage"
  - "add user nav with key ssh-ed25519 AAAA..."
  - "deploy my react app"
  - "show failed services"
  - "update nginx"

Run:
    python server_cli.py

Type 'exit' or 'quit' to disconnect.
"""

import getpass
import sys
from loguru import logger

# Silence info logs in CLI mode — show only errors
logger.remove()
logger.add(sys.stderr, level="ERROR")


def ask_credentials() -> dict:
    print("\n" + "═" * 60)
    print("  Server Management CLI")
    print("═" * 60)
    host     = input("  Host     (e.g. 127.0.0.1):          ").strip() or "127.0.0.1"
    port     = input("  SSH port (default 2222):             ").strip() or "2222"
    username = input("  Username:                            ").strip()
    password = getpass.getpass("  Password:                            ")
    key_file = input("  SSH key  (Enter to skip):            ").strip() or None

    config = {
        "host":     host,
        "port":     int(port),
        "username": username,
        "password": password,
    }
    if key_file:
        config["key_filename"] = key_file
    return config


def run_query(query: str, executor_config: dict) -> str:
    """Route query to the right agent and return the result."""
    from app.agents.supervisor import supervisor

    decision   = supervisor.route(query)
    agent_name = decision.get("agent", "general")

    label = executor_config.get("host", "server")

    if agent_name == "setup":
        from app.agents.setup_agent import SetupAgent
        agent = SetupAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    elif agent_name == "user_management":
        from app.agents.user_management_agent import UserManagementAgent
        agent = UserManagementAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    elif agent_name == "deployment":
        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(executor_type="ssh", executor_config=executor_config)
        return agent.execute_task(query)

    elif agent_name == "monitoring":
        from app.agents.monitoring_agent import MonitoringAgent
        agent = MonitoringAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.check_all()

    elif agent_name == "security":
        from app.agents.security_agent import SecurityAgent
        agent = SecurityAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.full_audit()

    elif agent_name == "maintenance":
        from app.agents.maintenance_agent import MaintenanceAgent
        agent = MaintenanceAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    elif agent_name == "troubleshooting":
        from app.agents.troubleshooting_agent import TroubleshootingAgent
        agent = TroubleshootingAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    else:
        # General — run a quick server scan and answer from context
        from app.agents.setup_agent import SetupAgent
        agent = SetupAgent(executor_type="ssh", executor_config=executor_config, server_label=label)
        installed = agent._scan_server()
        lines = ["Current server state:"]
        for item, version in installed.items():
            status = f"✓  {version}" if version else "✗  not installed"
            lines.append(f"  {item:<20} {status}")
        return "\n".join(lines)


def main():
    executor_config = ask_credentials()
    host = executor_config["host"]
    user = executor_config.get("username", "user")

    # Test connection
    print(f"\n  Connecting to {user}@{host}...")
    try:
        from app.agents.setup_agent import SetupAgent
        test_agent = SetupAgent(
            executor_type="ssh",
            executor_config=executor_config,
            server_label=host
        )
        out, err = test_agent._exec("whoami")
        if not out:
            raise Exception("No response from server")
        print(f"  ✓ Connected as: {out}")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        sys.exit(1)

    print("\n  Type your query. Examples:")
    print("    what's installed on this server?")
    print("    install docker")
    print("    show nginx config")
    print("    check disk usage")
    print("    add user nav with key ssh-ed25519 AAAA...")
    print("    Type 'exit' to quit\n")
    print("─" * 60)

    while True:
        try:
            query = input(f"\n  [{user}@{host}] > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Disconnected.")
            break

        if not query:
            continue

        if query.lower() in ("exit", "quit", "q", "bye"):
            print("  Disconnected.")
            break

        # Handle "what's installed" / "show server state" locally without LLM
        if any(w in query.lower() for w in ("what's installed", "whats installed",
                                              "what is installed", "server state",
                                              "show installed", "list installed")):
            from app.agents.setup_agent import SetupAgent
            agent = SetupAgent(executor_type="ssh", executor_config=executor_config)
            installed = agent._scan_server()
            print("\n  Installed on server:")
            for item, version in installed.items():
                if version:
                    print(f"    ✓  {item:<20} {version}")
                else:
                    print(f"    ✗  {item:<20} not installed")
            continue

        try:
            result = run_query(query, executor_config)
            print()
            print(result)
        except Exception as e:
            print(f"\n  ✗ Error: {e}")


if __name__ == "__main__":
    main()
