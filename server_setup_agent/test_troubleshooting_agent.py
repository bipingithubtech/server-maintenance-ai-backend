from app.agents.troubleshooting_agent import TroubleshootingAgent

agent = TroubleshootingAgent(
    executor_type="ssh",
    executor_config={
        "host": "192.168.56.101",
        "username": "bipin123",
        "password": "bipin",
        "port": 22,
    },
    server_label="aiagent-192.168.56.101",
)

# Natural language — auto-routes to the right diagnosis
# print(agent.diagnose("502 bad gateway on ats-backend"))
# print(agent.diagnose("ats-backend keeps crashing"))
# print(agent.diagnose("high cpu usage"))
# print(agent.diagnose("disk is full"))
# print(agent.diagnose("nginx not working"))
# print(agent.diagnose("port 4001 in use"))
# print(agent.diagnose("database not connecting"))

# Specific diagnoses
# print(agent.diagnose_502("ats-backend"))
# print(agent.diagnose_app_crash("ats-backend"))
# print(agent.diagnose_high_cpu())
# print(agent.diagnose_disk_full())
# print(agent.diagnose_nginx())
# print(agent.diagnose_port_conflict("4001"))
# print(agent.diagnose_db_connection())

# Full report
print(agent.full_diagnosis())
