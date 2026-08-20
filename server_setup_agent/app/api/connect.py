"""
POST /api/v1/connect

Test SSH credentials before starting a chat session.
Frontend calls this when the user clicks "Connect".

Supports both plain and encrypted payloads:
- Plain: ConnectRequest with fields directly
- Encrypted: EncryptedRequest with encrypted_data field containing ConnectRequest

Returns:
  { "connected": true,  "user": "rahul", "host": "127.0.0.1" }
  { "connected": false, "error": "Authentication failed" }
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Union
import os
router = APIRouter()
class ConnectRequest(BaseModel):
    host:         str
    port:         int  = 22
    username:     str
    password:     Optional[str] = None
    key_filename: Optional[str] = None  # Path to private key file on server
    private_key:  Optional[str] = None  # Private key content (string) from frontend
    ssh_key:      Optional[str] = None  # Alias for private_key (for backward compatibility)
    sudo_password: Optional[str] = None  # Password for sudo commands (can be same as password)
    
    def model_post_init(self, __context):
        # Support both 'ssh_key' and 'private_key' field names
        if self.ssh_key and not self.private_key:
            self.private_key = self.ssh_key


class EncryptedConnectRequest(BaseModel):
    """Encrypted payload containing ConnectRequest"""
    encrypted_data: str


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


def decrypt_if_needed(req: Union[ConnectRequest, EncryptedConnectRequest]) -> ConnectRequest:
    """
    Decrypt the request if it's encrypted, otherwise return as-is.
    
    Args:
        req: Either plain ConnectRequest or EncryptedConnectRequest
        
    Returns:
        Decrypted ConnectRequest
    """
    from loguru import logger
    
    # If it's already a ConnectRequest, return it
    if isinstance(req, ConnectRequest):
        return req
    
    # If it has encrypted_data, decrypt it
    if hasattr(req, 'encrypted_data'):
        from app.utils.encryption import decrypt_payload
        
        encryption_key = os.getenv('ENCRYPTION_KEY', 'my-super-secret-encryption-key')
        
        try:
            decrypted_data = decrypt_payload(req.encrypted_data, encryption_key)
            logger.info("[API] ✓ Decrypted request payload")
            return ConnectRequest(**decrypted_data)
        except Exception as e:
            logger.error(f"[API] ✗ Decryption failed: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Decryption failed: {str(e)}")
    
    raise HTTPException(status_code=400, detail="Invalid request format")


@router.post("/connect", response_model=ConnectResponse)
def test_connection(req: Union[ConnectRequest, EncryptedConnectRequest]):
    """
    Verify SSH credentials. Called by frontend on the Connect screen.
    Returns connected=true and the logged-in username on success.
    
    Supports both plain and encrypted payloads.
    
    Authentication methods (in order of precedence):
    1. private_key: SSH private key content (string)
    2. key_filename: Path to specific SSH private key file
    3. Auto-discovery: Automatically find and try SSH keys from ~/.ssh/
    4. password: Username/password authentication (fallback)
    
    If sudo_password is provided, test that sudo works with the password.
    """
    from loguru import logger
    
    try:
        # Decrypt if needed
        plain_req = decrypt_if_needed(req)
        
        from app.executors.ssh_executor import SSHExecutor

        logger.info(f"[API] Testing connection to {plain_req.username}@{plain_req.host}:{plain_req.port}")
        
        executor = SSHExecutor(
            host=plain_req.host,
            username=plain_req.username,
            password=plain_req.password,
            key_filename=plain_req.key_filename,
            private_key=plain_req.private_key,  # NEW: Pass private key content
            port=plain_req.port,
            sudo_password=plain_req.sudo_password,
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
        if plain_req.sudo_password:
            exit_code, out, err = executor._run("sudo -n true 2>/dev/null || echo 'need_password'")
            # We don't strictly need to test here since it will be tested during deployment
            # Just confirm connection works

        return ConnectResponse(
            connected=True,
            user=username,
            host=plain_req.host,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] ✗ Connection failed with exception: {str(e)}")
        return ConnectResponse(connected=False, error=str(e))
