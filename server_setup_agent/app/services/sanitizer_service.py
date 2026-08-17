import re
from typing import Optional

_REGISTERED_SECRETS: list[str] = []


def register_credentials(
    host: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    key_filename: Optional[str] = None,
) -> None:
    """
    Register server credentials that must NEVER appear in LLM input/output.
    Call this once per request, before the agent is invoked.
    Clears the previous request's values first.

    NOTE: host and username are NOT treated as secrets — they appear legitimately
    in paths, commands and logs. Only password and key_filename are scrubbed.
    """
    global _REGISTERED_SECRETS
    _REGISTERED_SECRETS = [
        s for s in [password, key_filename]
        if s  # skip None / empty
    ]


def clear_credentials() -> None:
    """Remove all registered secrets (call after the request completes)."""
    global _REGISTERED_SECRETS
    _REGISTERED_SECRETS = []


def scrub_credentials(text: str) -> str:
    """
    Replace any registered credential value in *text* with [REDACTED].
    Safe to call on tool outputs before they are fed back to the LLM.
    """
    for secret in _REGISTERED_SECRETS:
        if secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text


class SanitizerService:

    @staticmethod
    def sanitize_command(command: str) -> str:
        """
        Cleans and normalises the command string before execution.
        - Strips leading/trailing whitespace
        - Removes the dangerous --no-preserve-root flag
        - Collapses repeated whitespace

        NOTE: We intentionally do NOT scrub credential values from the command
        string here. Registered secrets (host IP, username, etc.) may legitimately
        appear in commands (e.g. docker bind address 127.0.0.1, ssh targets).
        Scrubbing them corrupts valid commands. Credential scrubbing is applied
        only to command OUTPUT (stdout/stderr) in BaseExecutor.execute().
        """
        cmd = command.strip()
        cmd = cmd.replace("--no-preserve-root", "")
        cmd = re.sub(r'\s+', ' ', cmd).strip()
        return cmd
