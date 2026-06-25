from app.executors.base_executor import BaseExecutor
from app.executors.local_executor import LocalExecutor
from app.executors.ssh_executor import SSHExecutor

class ExecutorFactory:
    """
    Factory class to instantiate the appropriate executor based on type.
    """

    @staticmethod
    def get_executor(executor_type: str = "local", **kwargs) -> BaseExecutor:
        """
        Get an executor instance.

        Args:
            executor_type (str): "local" or "ssh"
            **kwargs: Additional parameters for the SSH connection 
                      (host, username, password, key_filename, port)

        Returns:
            BaseExecutor: An instance of either LocalExecutor or SSHExecutor
        """
        if executor_type == "local":
            return LocalExecutor()
        elif executor_type == "ssh":
            return SSHExecutor(**kwargs)
        else:
            raise ValueError(f"Unsupported executor type: {executor_type}")
