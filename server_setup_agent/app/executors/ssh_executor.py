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
        port: int = 22
    ):
        self.host = host
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self._local = threading.local()  # per-thread client

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            key_filename=self.key_filename,
            timeout=30,
            banner_timeout=60,
            auth_timeout=30,
        )
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
