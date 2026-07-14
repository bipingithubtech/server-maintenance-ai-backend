import yaml
import os
from typing import List

class SecurityViolationError(Exception):
    """Raised when a command violates the defined security policies."""
    pass

class PolicyService:
    def __init__(self, config_path: str = "configs/policies.yaml"):
        self.deny_list: List[str] = []
        self.allow_list: List[str] = []
        self._load_policies(config_path)

    def _load_policies(self, config_path: str):
        if not os.path.exists(config_path):
            
            return
            
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}
            self.deny_list = data.get('deny_list', [])
            self.allow_list = data.get('allow_list', [])

    def validate_command(self, command: str) -> bool:
        """
        Validates the command against the deny list.
        Raises SecurityViolationError if the command is blocked.

        Root-wipe patterns (rm -rf / and rm -rf /*) are matched with a
        word boundary so they don't fire on valid paths like /home/user/app.
        All other deny-list entries use plain substring matching.
        """
        import re as _re
        # Patterns that need precise boundary matching to avoid false positives
        ROOT_WIPE_PATTERNS = [
            r"rm\s+-rf\s+/\s*$",      # rm -rf /  (root only)
            r"rm\s+-rf\s+/\s+",       # rm -rf /  (space after slash)
            r"rm\s+-rf\s+/\*",        # rm -rf /*
        ]
        for pattern in ROOT_WIPE_PATTERNS:
            if _re.search(pattern, command):
                raise SecurityViolationError(
                    f"Command contains forbidden pattern: '{pattern}'"
                )

        # All other deny-list entries — plain substring match
        root_wipe = {"rm -rf /", "rm -rf /*"}
        for denied in self.deny_list:
            if denied in root_wipe:
                continue  # already handled above with regex
            if denied in command:
                raise SecurityViolationError(
                    f"Command contains forbidden pattern: '{denied}'"
                )
        return True
        return True


policy_service = PolicyService()
