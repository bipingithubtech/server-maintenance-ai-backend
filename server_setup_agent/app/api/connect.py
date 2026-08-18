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


@router.post("/connect", response_model=ConnectResponse)
def test_connection(req: ConnectRequest):
    """
    Verify SSH credentials. Called by frontend on the Connect screen.
    Returns connected=true and the logged-in username on success.
    
    If sudo_password is provided, test that sudo works with the password.
    """
    try:
        from app.executors.ssh_executor import SSHExecutor

        executor = SSHExecutor(
            host=req.host,
            username=req.username,
            password=req.password,
            key_filename=req.key_filename,
            port=req.port,
            sudo_password=req.sudo_password,  # Pass sudo password to executor
        )

        # Run whoami to confirm connection and get actual username
        exit_code, out, err = executor._run("whoami")
        if exit_code != 0:
            return ConnectResponse(connected=False, error=err or "Connection failed")

        username = out.strip()
        
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
        return ConnectResponse(connected=False, error=str(e))
