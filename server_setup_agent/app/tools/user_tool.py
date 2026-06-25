from app.executors.base_executor import BaseExecutor

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
        exit_code, out, err = self.executor.execute(f"sudo userdel -r {username}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to delete user {username}:\n{err}")
        return f"User {username} deleted successfully.\n{out}"

    def add_to_group(self, username: str, group: str) -> str:
        """Adds a user to a specific group."""
        exit_code, out, err = self.executor.execute(f"sudo usermod -aG {group} {username}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to add {username} to {group}:\n{err}")
        return f"User {username} added to group {group} successfully.\n{out}"
