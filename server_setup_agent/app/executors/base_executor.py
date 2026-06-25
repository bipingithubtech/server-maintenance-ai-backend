from abc import ABC, abstractmethod
from typing import Tuple

from app.services.sanitizer_service import SanitizerService
from app.services.policy_service import policy_service, SecurityViolationError

class BaseExecutor(ABC):
    """
    Abstract base class for all command executors (local, ssh, etc.).
    """

    def execute(self, command: str) -> Tuple[int, str, str]:
        """
        Execute a shell command with security checks.

        Args:
            command: The command string to execute.

        Returns:
            Tuple[int, str, str]: (exit_code, stdout, stderr)
        """
        try:
            sanitized_cmd = SanitizerService.sanitize_command(command)
            policy_service.validate_command(sanitized_cmd)
        except SecurityViolationError as e:
            return -1, "", str(e)
            
        return self._run(sanitized_cmd)

    @abstractmethod
    def _run(self, command: str) -> Tuple[int, str, str]:
        """
        Internal execution method implemented by subclasses.
        """
        pass
