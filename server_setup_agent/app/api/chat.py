from fastapi import APIRouter
from app.agents.supervisor import supervisor
from app.api.schemas import QueryRequest, QueryResponse

router = APIRouter()

@router.post("/query", response_model=QueryResponse)
async def handle_query(request: QueryRequest):
    """
    Single endpoint where all queries are sent.
    The supervisor analyzes the query and decides the correct route.
    """
    decision = supervisor.route(request.query)
    
    # In the future, based on `decision["agent"]`, we will invoke the
    # specific agent (like SetupAgent or DeploymentAgent) and pass
    # the `request.credentials.to_config()` securely to it.
    
    return decision
