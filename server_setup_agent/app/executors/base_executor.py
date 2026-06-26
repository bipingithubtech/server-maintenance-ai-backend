from abc import ABC, abstractmethod
from typing import Tuple

from app.services.sanitizer_service import SanitizerService, scrub_credentials
from app.services.policy_service import policy_service, SecurityViolationError


class BaseExecutor(ABC):
    """
    Abstract base class for all command executors (local, ssh, etc.).

    Security guarantees:
    - Commands are sanitized and policy-checked before execution.
    - stdout/stderr from every command are scrubbed of registered credential
      values (IP, username, password, key path) before being returned.
      This ensures that even if the OS echoes connection details in an error
      message, those values never reach the LLM.
    """

    def execute(self, command: str) -> Tuple[int, str, str]:
        """
        Execute a shell command with security checks.

        Args:
            command: The command string to execute.

        Returns:
            Tuple[int, str, str]: (exit_code, stdout, stderr)
            stdout and stderr are guaranteed to be free of registered secrets.
        """
        try:
            sanitized_cmd = SanitizerService.sanitize_command(command)
            policy_service.validate_command(sanitized_cmd)
        except SecurityViolationError as e:
            return -1, "", str(e)

        exit_code, stdout, stderr = self._run(sanitized_cmd)

        # Scrub any credential values from command output before returning.
        # The LLM sees these strings as tool results, so they must be clean.
        stdout = scrub_credentials(stdout)
        stderr = scrub_credentials(stderr)

        return exit_code, stdout, stderr

    @abstractmethod
    def _run(self, command: str) -> Tuple[int, str, str]:
        """
        Internal execution method implemented by subclasses.
        """
        pass
