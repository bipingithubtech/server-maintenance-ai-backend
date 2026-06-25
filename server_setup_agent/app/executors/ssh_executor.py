import paramiko
from typing import Tuple, Optional
from app.executors.base_executor import BaseExecutor

class SSHExecutor(BaseExecutor):
    """
    Executes commands on a remote server via SSH using Paramiko.
    """

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

    def _run(self, command: str) -> Tuple[int, str, str]:
        client = paramiko.SSHClient()
        # Automatically add unknown host keys (Note: in production you might want a stricter policy)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                key_filename=self.key_filename,
                timeout=10
            )
            stdin, stdout, stderr = client.exec_command(command)
            
            # Wait for command to finish and get exit code
            exit_code = stdout.channel.recv_exit_status()
            
            out = stdout.read().decode('utf-8')
            err = stderr.read().decode('utf-8')
            
            return exit_code, out, err
        except Exception as e:
            return -1, "", str(e)
        finally:
            client.close()
