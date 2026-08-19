import paramiko
import threading
from typing import Tuple, Optional
from app.executors.base_executor import BaseExecutor

# Default timeout for most commands (seconds).
# Build/install commands (npm install, npm run build, pip install) can take
# longer — they use LONG_RUNNING_TIMEOUT.
DEFAULT_TIMEOUT      = 120   # 2 min  — general commands
LONG_RUNNING_TIMEOUT = 1800  # 30 min — build / install commands (docker build can take 15+ min)

# Keywords that indicate a long-running command that needs more time
_LONG_RUNNING_KEYWORDS = (
    "npm install", "npm run build", "npm run", "yarn install", "yarn build",
    "pip install", "apt-get install", "apt-get update",
    "git clone", "git pull",
    "docker build", "docker pull", "docker run",
)


def _pick_timeout(command: str) -> int:
    cmd_lower = command.lower()
    for kw in _LONG_RUNNING_KEYWORDS:
        if kw in cmd_lower:
            return LONG_RUNNING_TIMEOUT
    return DEFAULT_TIMEOUT


class SSHExecutor(BaseExecutor):
    def __init__(
        self,
        host: str,
        username: str,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        port: int = 22,
        sudo_password: Optional[str] = None
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.sudo_password = sudo_password  # Password for sudo commands
        self._local = threading.local()  # per-thread client
        
        # Auto-discover SSH keys if not provided
        if key_filename:
            self.key_filename = key_filename
        else:
            # Look for common SSH key files in ~/.ssh/
            self.key_filename = self._discover_ssh_keys()
    
    def _discover_ssh_keys(self) -> Optional[list]:
        """
        Automatically discover SSH keys from the mounted ~/.ssh directory.
        Returns a list of potential key files for paramiko to try.
        """
        import os
        from loguru import logger
        
        ssh_dir = os.path.expanduser("~/.ssh")
        common_key_names = [
            "id_ed25519",
            "id_rsa",
            "id_ecdsa",
            "id_dsa",
        ]
        
        found_keys = []
        if os.path.exists(ssh_dir):
            for key_name in common_key_names:
                key_path = os.path.join(ssh_dir, key_name)
                if os.path.exists(key_path) and os.path.isfile(key_path):
                    found_keys.append(key_path)
                    logger.debug(f"[SSH] Found SSH key: {key_path}")
        
        if found_keys:
            logger.info(f"[SSH] Auto-discovered {len(found_keys)} SSH key(s): {', '.join(found_keys)}")
            return found_keys
        else:
            logger.debug(f"[SSH] No SSH keys found in {ssh_dir}")
            return None
    
    def get_public_key_content(self, private_key_path: str) -> Optional[str]:
        """
        Read the public key content from the .pub file corresponding to a private key.
        Returns the public key string or None if not found.
        """
        import os
        from loguru import logger
        
        pub_key_path = f"{private_key_path}.pub"
        
        try:
            if os.path.exists(pub_key_path):
                with open(pub_key_path, 'r') as f:
                    public_key = f.read().strip()
                logger.debug(f"[SSH] Read public key from {pub_key_path}")
                return public_key
            else:
                logger.warning(f"[SSH] Public key file not found: {pub_key_path}")
                return None
        except Exception as e:
            logger.error(f"[SSH] Error reading public key: {e}")
            return None
    
    def get_all_public_keys(self) -> dict:
        """
        Get all discovered public keys with their content.
        Returns a dict mapping key paths to their public key content.
        """
        public_keys = {}
        
        if isinstance(self.key_filename, list):
            for key_path in self.key_filename:
                pub_key = self.get_public_key_content(key_path)
                if pub_key:
                    public_keys[key_path] = pub_key
        elif self.key_filename:
            pub_key = self.get_public_key_content(self.key_filename)
            if pub_key:
                public_keys[self.key_filename] = pub_key
        
        return public_keys

    def _connect(self) -> paramiko.SSHClient:
        from loguru import logger
        import os
        
        client = paramiko.SSHClient()
        # Accept all host keys automatically (ignore host key verification)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Also load system host keys if available
        try:
            client.load_system_host_keys()
        except Exception as e:
            logger.debug(f"[SSH] Could not load system host keys: {e}")
        
        # Determine authentication method
        auth_method = "key" if self.key_filename else "password" if self.password else "agent/default"
        logger.info(f"[SSH] Connecting to {self.username}@{self.host}:{self.port} using {auth_method} authentication")
        
        # Log key files if provided
        if self.key_filename:
            if isinstance(self.key_filename, list):
                logger.debug(f"[SSH] Using key files: {', '.join(self.key_filename)}")
            else:
                if os.path.exists(self.key_filename):
                    logger.debug(f"[SSH] Using key file: {self.key_filename}")
                else:
                    logger.warning(f"[SSH] ⚠ Key file not found: {self.key_filename}")
        
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                key_filename=self.key_filename,
                timeout=30,
                banner_timeout=60,
                auth_timeout=30,
                look_for_keys=True,  # Look for SSH keys in ~/.ssh/
                allow_agent=True,     # Allow SSH agent
                disabled_algorithms=dict()  # Don't disable any algorithms
            )
            logger.info(f"[SSH] ✓ Connected successfully to {self.host}")
        except paramiko.AuthenticationException as e:
            error_msg = str(e)
            logger.error(f"[SSH] ✗ Authentication failed for {self.username}@{self.host}: {error_msg}")
            
            # Provide helpful error message based on error type
            if "publickey" in error_msg.lower():
                raise Exception(f"Authentication failed: Server only accepts SSH key authentication. Password login is disabled. Please provide a valid SSH private key.")
            elif self.key_filename:
                raise Exception(f"Authentication failed: SSH key rejected. Verify the key is authorized on the server.")
            else:
                raise Exception(f"Authentication failed: Invalid username or password")
        except paramiko.SSHException as e:
            logger.error(f"[SSH] ✗ SSH error connecting to {self.host}: {e}")
            raise Exception(f"SSH connection error: {e}")
        except Exception as e:
            logger.error(f"[SSH] ✗ Failed to connect to {self.host}:{self.port}: {e}")
            raise Exception(f"Connection failed: {e}")
        
        # Keep connection alive every 30s to prevent server dropping idle SSH
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)
        return client

    def _is_alive(self, client: paramiko.SSHClient) -> bool:
        try:
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                return False
            transport.send_ignore()
            return True
        except Exception:
            return False

    def _get_client(self) -> paramiko.SSHClient:
        client = getattr(self._local, "client", None)
        if client is None or not self._is_alive(client):
            self._local.client = self._connect()
        return self._local.client

    def _exec(self, client: paramiko.SSHClient, command: str) -> Tuple[int, str, str]:
        """Run a single command with an appropriate timeout."""
        timeout = _pick_timeout(command)
        
        # If command uses sudo and sudo_password is available, inject password via stdin
        if "sudo" in command and self.sudo_password:
            from loguru import logger
            logger.info(f"[SSH] ✓ Sudo password available - using stdin injection with PTY")
            logger.debug(f"[SSH] Original command: {command[:80]}...")
            
            # Replace "sudo" with "sudo -S" to read password from stdin
            command = command.replace("sudo ", "sudo -S ", 1)
            logger.debug(f"[SSH] Modified command: {command[:80]}...")
            
            # Execute command with PTY allocation and write password to stdin
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=True)
            
            # Small delay to ensure sudo prompt is ready
            import time
            time.sleep(0.1)
            
            # Write password and newline
            stdin.write(f"{self.sudo_password}\n")
            stdin.flush()
            
            stdout.channel.setblocking(True)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            
            # When using PTY, stderr is merged into stdout, so extract actual errors
            # Remove sudo password prompt from output
            out = out.replace(f"[sudo] password for {self.username}: ", "")
            
            return exit_code, out, err
        elif "sudo" in command and not self.sudo_password:
            from loguru import logger
            logger.warning(f"[SSH] ✗ Command needs sudo but no sudo_password available: {command[:50]}...")
        
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdout.channel.setblocking(True)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return exit_code, out, err

    def _run(self, command: str) -> Tuple[int, str, str]:
        try:
            client = self._get_client()
            return self._exec(client, command)
        except Exception as e:
            # Force reconnect and retry once
            self._local.client = None
            try:
                client = self._get_client()
                return self._exec(client, command)
            except Exception as retry_e:
                self._local.client = None
                return -1, "", str(retry_e)
