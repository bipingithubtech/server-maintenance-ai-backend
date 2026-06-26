from fastapi import APIRouter, HTTPException
from loguru import logger

from app.agents.supervisor import supervisor
from app.api.schemas import QueryRequest, QueryResponse
from app.services.sanitizer_service import register_credentials, clear_credentials

router = APIRouter()


def _dispatch(agent_name: str, query: str, request: QueryRequest) -> str:
    """
    Instantiate the correct agent and run the task.

    Credentials are extracted here and passed ONLY to the Executor via
    executor_config.  They are never included in the query string or any
    message that reaches the LLM.
    """
    creds = request.credentials
    executor_type = creds.executor_type if creds else "local"
    executor_config = creds.to_config() if creds else {}

    if agent_name == "deployment":
        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(executor_type=executor_type, executor_config=executor_config)
        return agent.execute_task(query)

    if agent_name == "setup":
        from app.agents.setup_agent import SetupAgent
        agent = SetupAgent(executor_type=executor_type, executor_config=executor_config)
        return agent.execute_task(query)

    # Remaining agents are stubs — return a clear placeholder so the frontend
    # knows the feature is coming rather than receiving a silent empty response.
    return (
        f"The '{agent_name}' agent is not yet implemented. "
        "Only 'deployment' and 'setup' are active right now."
    )


@router.post("/query", response_model=QueryResponse)
async def handle_query(request: QueryRequest):
    """
    Single entry-point for all agent requests.

    Flow:
      1. Supervisor classifies the query → picks an agent
      2. Credentials are registered with the sanitizer so they can never
         leak into LLM messages or tool outputs
      3. The correct agent is dispatched; credentials stay inside the Executor
      4. Credentials are cleared after the request completes
    """
    # ------------------------------------------------------------------ #
    # 1. Register credentials with the sanitizer BEFORE any LLM call.    #
    #    This ensures that if a credential value ever appears in a tool   #
    #    output that gets fed back to the LLM, it is replaced with        #
    #    [REDACTED] automatically.                                        #
    # ------------------------------------------------------------------ #
    creds = request.credentials
    if creds:
        register_credentials(
            host=creds.host,
            username=creds.username,
            password=creds.password,
            key_filename=creds.key_filename,
        )

    try:
        # ---------------------------------------------------------------- #
        # 2. Supervisor classifies intent.                                 #
        #    Only the user's query text is sent — no credentials.         #
        # ---------------------------------------------------------------- #
        decision = supervisor.route(request.query)
        agent_name: str = decision.get("agent", "general")
        reason: str = decision.get("reason", "")

        logger.info(f"Supervisor routed to '{agent_name}': {reason}")

        # ---------------------------------------------------------------- #
        # 3. Dispatch to agent.                                            #
        # ---------------------------------------------------------------- #
        result = _dispatch(agent_name, request.query, request)

        return QueryResponse(agent=agent_name, reason=reason, result=result)

    except Exception as exc:
        logger.exception("Unhandled error in handle_query")
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # ---------------------------------------------------------------- #
        # 4. Always clear credentials when the request is done.           #
        # ---------------------------------------------------------------- #
        clear_credentials()
