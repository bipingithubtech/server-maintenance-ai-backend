from app.executors.base_executor import BaseExecutor

class SSHTool:
    """
    Tool for managing SSH keys and configurations.
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def generate_keypair(self, email: str = "") -> str:
        """
        Generates a new Ed25519 SSH keypair without a passphrase.
        """
        cmd = "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -q"
        if email:
            cmd = f"ssh-keygen -t ed25519 -C '{email}' -N '' -f ~/.ssh/id_ed25519 -q"
            
        exit_code, out, err = self.executor.execute(cmd)
        # If key already exists, ssh-keygen exits with non-zero or prompts. We force non-interactive.
        # A better approach in production is checking if the file exists first.
        if exit_code != 0:
            # Check if it failed because it already exists
            code, _, _ = self.executor.execute("test -f ~/.ssh/id_ed25519")
            if code == 0:
                return "SSH keypair already exists."
            raise RuntimeError(f"Failed to generate SSH key:\n{err}")
        return "SSH keypair generated successfully."

    def get_public_key(self) -> str:
        """
        Reads and returns the public Ed25519 SSH key.
        """
        exit_code, out, err = self.executor.execute("cat ~/.ssh/id_ed25519.pub")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read public key:\n{err}")
        return out.strip()

    def add_authorized_key(self, public_key: str) -> str:
        """
        Appends a provided public key string to the authorized_keys file.
        """
        cmd = f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{public_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to add authorized key:\n{err}")
        return "Public key added to authorized_keys."
