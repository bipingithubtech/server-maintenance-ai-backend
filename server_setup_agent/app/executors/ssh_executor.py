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
        self.key_filename = key_filename
        self.port = port
        self.sudo_password = sudo_password  # Password for sudo commands
        self._local = threading.local()  # per-thread client

    def _connect(self) -> paramiko.SSHClient:
        from loguru import logger
        
        client = paramiko.SSHClient()
        # Accept all host keys automatically (ignore host key verification)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Also load system host keys if available
        try:
            client.load_system_host_keys()
        except Exception as e:
            logger.debug(f"[SSH] Could not load system host keys: {e}")
        
        logger.info(f"[SSH] Connecting to {self.username}@{self.host}:{self.port}")
        
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
                look_for_keys=True,  # Look for SSH keys
                allow_agent=True,     # Allow SSH agent
                disabled_algorithms=dict()  # Don't disable any algorithms
            )
            logger.info(f"[SSH] ✓ Connected successfully to {self.host}")
        except paramiko.AuthenticationException as e:
            logger.error(f"[SSH] ✗ Authentication failed for {self.username}@{self.host}: {e}")
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
