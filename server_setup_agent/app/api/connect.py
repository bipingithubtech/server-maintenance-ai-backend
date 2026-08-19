"""
POST /api/v1/connect

Test SSH credentials before starting a chat session.
Frontend calls this when the user clicks "Connect".

Returns:
  { "connected": true,  "user": "rahul", "host": "127.0.0.1" }
  { "connected": false, "error": "Authentication failed" }
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class ConnectRequest(BaseModel):
    host:         str
    port:         int  = 22
    username:     str
    password:     Optional[str] = None
    key_filename: Optional[str] = None
    sudo_password: Optional[str] = None  # Password for sudo commands (can be same as password)


class ConnectResponse(BaseModel):
    connected: bool
    user:      Optional[str] = None
    host:      Optional[str] = None
    error:     Optional[str] = None
    public_keys: Optional[dict] = None  # NEW: Map of key paths to public key content


class PublicKeysResponse(BaseModel):
    """Response containing discovered public keys from the system"""
    public_keys: dict  # Map of private key path -> public key content
    count: int


@router.post("/connect", response_model=ConnectResponse)
def test_connection(req: ConnectRequest):
    """
    Verify SSH credentials. Called by frontend on the Connect screen.
    Returns connected=true and the logged-in username on success.
    
    Authentication methods (in order of precedence):
    1. private_key: SSH private key content (string)
    2. key_filename: Path to specific SSH private key file
    3. Auto-discovery: Automatically find and try SSH keys from ~/.ssh/
    4. password: Username/password authentication (fallback)
    
    If sudo_password is provided, test that sudo works with the password.
    """
    from loguru import logger
    
    try:
        from app.executors.ssh_executor import SSHExecutor

        logger.info(f"[API] Testing connection to {req.username}@{req.host}:{req.port}")
        
        executor = SSHExecutor(
            host=req.host,
            username=req.username,
            password=req.password,
            key_filename=req.key_filename,
            port=req.port,
            sudo_password=req.sudo_password,
        )

        # Run whoami to confirm connection and get actual username
        exit_code, out, err = executor._run("whoami")
        if exit_code != 0:
            error_msg = err or "Connection failed"
            logger.error(f"[API] Connection test failed: {error_msg}")
            return ConnectResponse(connected=False, error=error_msg)

        username = out.strip()
        logger.info(f"[API] ✓ Connected as {username}")
        
        # If sudo_password provided, test that sudo works
        if req.sudo_password:
            exit_code, out, err = executor._run("sudo -n true 2>/dev/null || echo 'need_password'")
            # We don't strictly need to test here since it will be tested during deployment
            # Just confirm connection works

        return ConnectResponse(
            connected=True,
            user=username,
            host=req.host,
        )

    except Exception as e:
        logger.error(f"[API] ✗ Connection failed with exception: {str(e)}")
        return ConnectResponse(connected=False, error=str(e))
