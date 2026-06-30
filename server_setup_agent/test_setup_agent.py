from app.agents.setup_agent import SetupAgent

agent = SetupAgent(
    executor_type="ssh",
    executor_config={
        "host": "192.168.56.101",
        "username": "bipin123",
        "password": "bipin",
        "port": 22,
    },
    server_label="aiagent-192.168.56.101",
)

# Tell it what you want — it will show you a plan and ask for extras
result = agent.execute_task(
    "setup a web server with nginx, nodejs, pm2, docker, ssh_harden, fail2ban, and python"
)


# Or for full setup:
# result = agent.execute_task("full server setup")

# Or minimal:
# result = agent.execute_task("just install nginx and firewall")
