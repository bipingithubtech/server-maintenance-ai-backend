import re
from typing import Optional


# Module-level registry of secrets to scrub from anything sent to the LLM.
# Populated once at request time by register_credentials(); never stored elsewhere.
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
    """
    global _REGISTERED_SECRETS
    _REGISTERED_SECRETS = [
        s for s in [host, username, password, key_filename]
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
        - Strips any accidentally embedded credential values
        """
        cmd = command.strip()
        cmd = cmd.replace("--no-preserve-root", "")
        cmd = re.sub(r'\s+', ' ', cmd).strip()

        # Belt-and-suspenders: if a credential somehow ended up in the command
        # string (e.g. the LLM hallucinated a password), strip it out.
        cmd = scrub_credentials(cmd)

        return cmd
