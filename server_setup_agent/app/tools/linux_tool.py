from app.executors.base_executor import BaseExecutor

class LinuxTool:
    """
    Tool for generic Linux system commands like file manipulation and permissions.
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def run_custom_command(self, command: str) -> str:
        """
        Executes any custom shell command. Use with caution.
        """
        exit_code, out, err = self.executor.execute(command)
        if exit_code != 0:
            raise RuntimeError(f"Command failed:\n{err}")
        return out

    def get_os_info(self) -> str:
        """
        Gets information about the Linux distribution from /etc/os-release.
        """
        exit_code, out, err = self.executor.execute("cat /etc/os-release")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get OS info:\n{err}")
        return out

    def change_permissions(self, path: str, permissions: str) -> str:
        """
        Changes file or directory permissions (chmod).
        Example permissions: '755', '+x'
        """
        exit_code, out, err = self.executor.execute(f"sudo chmod {permissions} {path}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to change permissions for {path}:\n{err}")
        return f"Permissions changed successfully to {permissions} for {path}."

    def change_owner(self, path: str, owner: str, group: str = None) -> str:
        """
        Changes file or directory ownership (chown).
        """
        target = f"{owner}:{group}" if group else owner
        exit_code, out, err = self.executor.execute(f"sudo chown {target} {path}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to change ownership for {path}:\n{err}")
        return f"Ownership changed successfully for {path}."
