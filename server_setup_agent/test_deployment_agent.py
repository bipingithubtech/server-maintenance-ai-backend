from app.agents.deployment_agent import DeploymentAgent

def main():
    agent = DeploymentAgent(
        executor_type="ssh",
        executor_config={
            "host": "127.0.0.1",
            "username": "bipin123",
            "password": "bipin",
            "port": 2222
        }
    )

    query = "Deploy my application to the server git@github.com:Meetri-IT/voice_ai_frontend.git. Stack is Vite/React."

    print(f"--- Sending query ---\n{query}\n")  
    response = agent.execute_task(query)
    print(f"--- Agent response ---\n{response}\n")

if __name__ == "__main__":
    main()
