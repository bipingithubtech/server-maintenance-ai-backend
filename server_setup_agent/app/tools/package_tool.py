from app.executors.base_executor import BaseExecutor


# one lock across all threads - only one apt command runs at a time
import threading
_APT_LOCK = threading.Lock()


class PackageTool:
    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def _apt(self, command: str, error_msg: str) -> tuple[int, str, str]:
        """Run any apt/dpkg command serially - never in parallel."""
        with _APT_LOCK:
            return self.executor.execute(command)

    def update_lists(self) -> str:
        exit_code, stdout, stderr = self._apt(
            "sudo apt-get update -y",
            "Failed to update package list"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to update package list. Error:\n{stderr}")
        return "Package lists updated successfully.\n" + stdout

    def install(self, package_name: str) -> str:
        exit_code, stdout, stderr = self._apt(
            f"sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {package_name}",
            f"Failed to install {package_name}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to install {package_name}. Error:\n{stderr}")
        return f"Package {package_name} installed successfully.\n" + stdout

    def remove(self, package_name: str) -> str:
        exit_code, stdout, stderr = self._apt(
            f"sudo apt-get remove -y {package_name}",
            f"Failed to remove {package_name}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to remove {package_name}. Error:\n{stderr}")
        return f"Package {package_name} removed successfully.\n" + stdout

    def is_installed(self, package_name: str) -> bool:
        exit_code, stdout, stderr = self._apt(
            f"dpkg -s {package_name}",
            ""
        )
        return exit_code == 0 and "Status: install ok installed" in stdout