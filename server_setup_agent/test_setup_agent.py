from app.agents.setup_agent import SetupAgent

def main():
    agent = SetupAgent(
        executor_type="ssh",
        executor_config={
            "host": "192.168.56.101",
            "username": "bipin123",
            "password": "bipin",
            "port": 22
        }
    )

    query = "setup the server"

    print(f"--- Sending query ---\n{query}\n")
    response = agent.execute_task(query)
    print(f"--- Agent response ---\n{response}\n")

if __name__ == "__main__":
    main()
