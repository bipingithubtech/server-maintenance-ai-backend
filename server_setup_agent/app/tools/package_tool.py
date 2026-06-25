from app.executors.base_executor import BaseExecutor

class PackageTool:
    """
    Tool for managing system packages using the apt package manager (Debian/Ubuntu based).
    Relies on the executor layer to run commands locally or remotely.
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def update_lists(self) -> str:
        """
        Updates the apt package index.
        """
        exit_code, stdout, stderr = self.executor.execute("sudo apt-get update -y")
        if exit_code != 0:
            raise RuntimeError(f"Failed to update package list. Error:\n{stderr}")
        return "Package lists updated successfully.\n" + stdout

    def install(self, package_name: str) -> str:
        """
        Installs a given package.
        """
        exit_code, stdout, stderr = self.executor.execute(f"sudo apt-get install -y {package_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to install {package_name}. Error:\n{stderr}")
        return f"Package {package_name} installed successfully.\n" + stdout

    def remove(self, package_name: str) -> str:
        """
        Removes a given package.
        """
        exit_code, stdout, stderr = self.executor.execute(f"sudo apt-get remove -y {package_name}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to remove {package_name}. Error:\n{stderr}")
        return f"Package {package_name} removed successfully.\n" + stdout

    def is_installed(self, package_name: str) -> bool:
        """
        Checks if a package is currently installed.
        """
        exit_code, stdout, stderr = self.executor.execute(f"dpkg -s {package_name}")
        
        # 'dpkg -s' returns 0 if the package is installed, 1 if not.
        # We also ensure the status explicitly mentions it is installed.
        if exit_code == 0 and "Status: install ok installed" in stdout:
            return True
        return False
