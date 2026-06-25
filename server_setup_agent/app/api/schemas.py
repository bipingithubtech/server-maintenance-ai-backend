from pydantic import BaseModel
from typing import Optional, Dict, Any

class ServerCredentials(BaseModel):
    """
    Secure schema to accept target server credentials from the frontend.
    These are passed directly to the Executor and never exposed to the LLM.
    """
    executor_type: str = "local" # 'local' or 'ssh'
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_filename: Optional[str] = None
    port: int = 22

    def to_config(self) -> Dict[str, Any]:
        """Converts to a kwargs dictionary suitable for ExecutorFactory."""
        return {
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "key_filename": self.key_filename,
            "port": self.port
        }

class QueryRequest(BaseModel):
    query: str
    credentials: Optional[ServerCredentials] = ServerCredentials()

class QueryResponse(BaseModel):
    agent: str
    reason: str
