import subprocess
from typing import Tuple
from app.executors.base_executor import BaseExecutor

class LocalExecutor(BaseExecutor):
    """
    Executes commands on the local server where the agent is running.
    """
    
    def _run(self, command: str) -> Tuple[int, str, str]:
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            return -1, "", str(e)
