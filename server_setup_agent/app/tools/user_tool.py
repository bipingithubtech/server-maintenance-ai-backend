from app.executors.base_executor import BaseExecutor
from loguru import logger

class UserTool:
    """Tool for managing system users and groups."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def create_user(self, username: str) -> str:
        """Creates a new user account."""
        exit_code, out, err = self.executor.execute(f"sudo useradd -m {username}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to create user {username}:\n{err}")
        return f"User {username} created successfully.\n{out}"

    def delete_user(self, username: str) -> str:
        """Deletes a user account and their home directory."""
        # SAFETY: This is a destructive operation
        # Log the attempt but don't execute - require explicit confirmation
        logger.critical(f"[SECURITY] DELETE USER ATTEMPT: {username}")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="⚠️ DELETE USER REQUESTED",
            server="Server",
            details=f"User deletion request: {username}\n\n"
                   f"This is a destructive operation.\n"
                   f"REQUIRES EXPLICIT CONFIRMATION from administrator."
        )
        
        raise RuntimeError(
            f"❌ BLOCKED: User deletion is a destructive operation.\n\n"
            f"User to delete: {username}\n\n"
            f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
            f"To proceed, you must:\n"
            f"1. Verify this is intentional\n"
            f"2. Get explicit approval from security admin\n"
            f"3. Run manually via SSH:\n"
            f"   sudo userdel -r {username}"
        )

    def add_to_group(self, username: str, group: str) -> str:
        """Adds a user to a specific group."""
        exit_code, out, err = self.executor.execute(f"sudo usermod -aG {group} {username}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to add {username} to {group}:\n{err}")
        return f"User {username} added to group {group} successfully.\n{out}"
