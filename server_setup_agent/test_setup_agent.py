from app.agents.setup_agent import SetupAgent

def main():
    agent = SetupAgent(
        executor_type="ssh",
        executor_config={
            "host": "127.0.0.1",
            "username": "bipin123",
            "password": "bipin",
            "port": 2222
        }
    )

    query = "Update the package lists, then tell me what you did. Do not install anything."

    print(f"--- Sending query ---\n{query}\n")
    response = agent.execute_task(query)
    print(f"--- Agent response ---\n{response}\n")

if __name__ == "__main__":
    main()
