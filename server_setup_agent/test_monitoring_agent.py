from app.agents.monitoring_agent import MonitoringAgent

agent = MonitoringAgent(
    executor_type="ssh",
    executor_config={
        "host": "192.168.56.101",
        "username": "bipin123",
        "password": "bipin",
        "port": 22,
    },
    server_label="aiagent-192.168.56.101",
)

# Full check — all apps + system
print(agent.check_all())

# Single app check
# print(agent.check_app("ats-backend", port="4001"))

# Logs
# print(agent.get_logs("ats-backend", lines=20))

# System only
# print(agent.get_system_summary())
