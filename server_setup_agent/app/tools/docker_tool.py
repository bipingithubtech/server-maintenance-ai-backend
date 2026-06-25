from app.executors.base_executor import BaseExecutor

class DockerTool:
    """
    Tool for managing Docker installation and services.
    Relies on the executor layer to run commands locally or remotely.
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def is_installed(self) -> bool:
        """
        Checks if Docker is installed.
        """
        exit_code, stdout, stderr = self.executor.execute("docker --version")
        return exit_code == 0

    def install(self) -> str:
        """
        Installs Docker using the official convenience script.
        """
        # Download the script and execute it
        command = "curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh"
        exit_code, stdout, stderr = self.executor.execute(command)
        
        if exit_code != 0:
            raise RuntimeError(f"Failed to install Docker. Error:\n{stderr}")
            
        return f"Docker installed successfully.\n{stdout}"

    def start_service(self) -> str:
        """
        Starts and enables the Docker systemd service.
        """
        command = "sudo systemctl start docker && sudo systemctl enable docker"
        exit_code, stdout, stderr = self.executor.execute(command)
        
        if exit_code != 0:
            raise RuntimeError(f"Failed to start Docker service. Error:\n{stderr}")
            
        return "Docker service started and enabled successfully."

    def check_status(self) -> str:
        """
        Checks the status of the Docker service.
        """
        exit_code, stdout, stderr = self.executor.execute("sudo systemctl status docker --no-pager")
        if exit_code != 0:
            # systemctl status might return non-zero if inactive/failed, but we still want the output
            return f"Docker status check failed or inactive:\n{stdout}\n{stderr}"
        return stdout
