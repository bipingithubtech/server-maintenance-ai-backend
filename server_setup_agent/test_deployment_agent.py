from app.agents.deployment_agent import DeploymentAgent

# You can provide everything upfront or leave things out —
# the agent will ask for whatever is missing.
QUERY = "deploy https://github.com/Meetri-IT/luna-frontend.git stack is next js ip is 192.168.56.101"


def main():
    agent = DeploymentAgent(
        executor_type="ssh",
        executor_config={
            "host": "192.168.56.101",
            "username": "bipin123",
            "password": "bipin",
            "port": 22,
        },
    )
    print(f"\nQuery: {QUERY}\n{'=' * 60}\n")
    result = agent.execute_task(QUERY)
    print(f"\n{result}")


if __name__ == "__main__":
    main()
