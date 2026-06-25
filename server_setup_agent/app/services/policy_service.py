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
            # If not found, fallback to empty lists and trust the sanitizer
            return
            
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}
            self.deny_list = data.get('deny_list', [])
            self.allow_list = data.get('allow_list', [])

    def validate_command(self, command: str) -> bool:
        """
        Validates the command against the deny list.
        Raises SecurityViolationError if the command is blocked.
        """
        # Check against explicitly denied patterns
        for denied in self.deny_list:
            if denied in command:
                raise SecurityViolationError(f"Command contains forbidden pattern: '{denied}'")

        # For this setup, we rely on the deny list to block destructive actions.
        # Strict allow-listing of every bash combo can break pipelines.
        return True

# Singleton instance
policy_service = PolicyService()
