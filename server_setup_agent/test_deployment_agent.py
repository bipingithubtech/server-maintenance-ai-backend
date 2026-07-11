from app.agents.deployment_agent import DeploymentAgent

QUERY = "deploy https://github.com/Meetri-IT/voice_ai_backend.git stack=fastapi port=8001 domain=192.168.56.101"

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
