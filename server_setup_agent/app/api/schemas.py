from pydantic import BaseModel, model_validator
from typing import Optional, Dict, Any
from app.core.config import settings


class EncryptedRequest(BaseModel):
    """Request model for encrypted payloads from frontend"""
    encrypted_data: str


class ServerCredentials(BaseModel):
    """
    Secure schema to accept target server credentials from the frontend.
    These are passed directly to the Executor and never exposed to the LLM.
    """
    executor_type: str = "local"  # 'local' or 'ssh'
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_filename: Optional[str] = None  # Path to private key file on server
    private_key: Optional[str] = None   # Private key content (string) from frontend
    ssh_key: Optional[str] = None       # Alias for private_key (for backward compatibility)
    port: int = 22
    github_token: Optional[str] = None  # PAT for cloning private GitHub repos
    sudo_password: Optional[str] = None  # Password for sudo commands (can be same as password)
    
    def model_post_init(self, __context):
        # Support both 'ssh_key' and 'private_key' field names
        if self.ssh_key and not self.private_key:
            self.private_key = self.ssh_key

    @model_validator(mode="after")
    def apply_ssh_defaults(self) -> "ServerCredentials":
        import os
        if self.executor_type == "ssh":
            if not self.host:
                self.host = settings.SSH_HOST
            if not self.username:
                self.username = settings.SSH_USERNAME
            if self.port == 22 and settings.SSH_PORT != 22:
                self.port = settings.SSH_PORT
            # Apply password default only if no key is provided
            if not self.key_filename and not self.password:
                if settings.SSH_KEY_PATH:
                    self.key_filename = settings.SSH_KEY_PATH
                elif settings.SSH_PASSWORD:
                    self.password = settings.SSH_PASSWORD
            # Load sudo_password from environment if not provided
            if not self.sudo_password:
                self.sudo_password = os.getenv("SUDO_PASSWORD")
        return self

    def to_config(self) -> Dict[str, Any]:
        return {
            "host":         self.host,
            "username":     self.username,
            "password":     self.password,
            "key_filename": self.key_filename,
            "private_key":  self.private_key,
            "port":         self.port,
            "sudo_password": self.sudo_password,
        }


class DeploymentParams(BaseModel):
    """
    Optional extra params for deployment — sent by frontend instead of
    interactive prompts. If omitted, agent falls back to prompting.
    """
    branch:          Optional[str] = None   # git branch to deploy
    process_manager: Optional[str] = None   # pm2 | systemd | docker
    env_vars:        Optional[Dict[str, str]] = None  # .env key-value pairs


class SetupParams(BaseModel):
    """
    Optional extra params for fresh server setup sent by frontend.
    If omitted, agent asks interactively.
    """
    new_username:     Optional[str] = None   # new sudo username
    new_password:     Optional[str] = None   # password for new user
    your_public_key:  Optional[str] = None   # SSH public key for new user


class QueryRequest(BaseModel):
    query:             str
    credentials:       Optional[ServerCredentials] = None
    deployment_params: Optional[DeploymentParams]  = None
    setup_params:      Optional[SetupParams]        = None
    conversation_id:   Optional[str] = None         # for multi-turn conversations

    @model_validator(mode="after")
    def default_credentials(self) -> "QueryRequest":
        if self.credentials is None:
            self.credentials = ServerCredentials()
        return self


class QueryResponse(BaseModel):
    agent:           str
    reason:          str
    result:          Optional[str] = None
    needs_input:     bool = False           # True when agent needs more info from user
    question:        Optional[str] = None   # The question to show in frontend chat
    conversation_id: Optional[str] = None   # ID to continue the conversation
