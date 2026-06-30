from app.agents.maintenance_agent import MaintenanceAgent

agent = MaintenanceAgent(
    executor_type="ssh",
    executor_config={
        "host": "192.168.56.101",
        "username": "bipin123",
        "password": "bipin",
        "port": 22,
    },
    server_label="aiagent-192.168.56.101",
)

# ── Pick one ───────────────────────────────────────────────────────────────────

# Update a single app (git pull → rebuild → restart)
# print(agent.update_app("ats-backend"))

# Rotate logs
# print(agent.rotate_logs("ats-backend"))

# Free disk space
# print(agent.clear_disk())

# Restart a specific service
# print(agent.restart_service("ats-backend"))          # auto-detect manager
# print(agent.restart_service("nginx", "systemd"))     # explicit systemd
# print(agent.restart_service("ats-backend", "pm2"))   # explicit pm2

# System packages update
# print(agent.system_update())

# Full maintenance — updates all apps + logs + disk + system
print(agent.full_maintenance())
# print(agent.full_maintenance(["ats-backend", "ats-frontend"]))  # specific apps
