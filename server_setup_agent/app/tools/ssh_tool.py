import shlex
import subprocess
import tempfile
from pathlib import Path

from app.executors.base_executor import BaseExecutor
from app.services.key_storage import get_key_storage_backend


class SSHTool:
    """Tool for managing SSH keys and configurations."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor
        self.key_storage = get_key_storage_backend()

    # ── REMOTE operations (run on the target server) ───────────────────────

    def get_public_key(self) -> str:
        """Reads the public Ed25519 SSH key from the remote server (if one exists there)."""
        exit_code, out, err = self.executor.execute("cat ~/.ssh/id_ed25519.pub")
        if exit_code != 0:
            raise RuntimeError(f"Failed to read public key:\n{err}")
        return out.strip()

    def add_authorized_key(self, public_key: str, home_dir: str = "~") -> str:
        """Safely appends a public key to authorized_keys on the remote server."""
        public_key = public_key.strip()
        if not public_key or "\n" in public_key:
            raise ValueError("Invalid public key — must be a single line.")

        quoted_key = shlex.quote(public_key)
        cmd = (
            f"mkdir -p {home_dir}/.ssh && chmod 700 {home_dir}/.ssh && "
            f"touch {home_dir}/.ssh/authorized_keys && "
            f"grep -qxF {quoted_key} {home_dir}/.ssh/authorized_keys || "
            f"echo {quoted_key} >> {home_dir}/.ssh/authorized_keys && "
            f"chmod 600 {home_dir}/.ssh/authorized_keys"
        )
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to add authorized key:\n{err}")
        return "Public key added to authorized_keys (deduplicated)."

    # ── LOCAL operations (run on the machine executing the agent) ──────────

    def generate_local_keypair(self, key_name: str, email: str = "") -> dict:
        """
        Generates an Ed25519 keypair LOCALLY (not on the remote server) so the
        private key never has to live on the target machine.

        The keypair is generated into a temp directory, the private key is
        handed off to the configured storage backend (local disk by default,
        Vault/AWS Secrets Manager when configured), and the temp copy is
        discarded automatically.

        Returns a dict with:
          - private_key_reference: path/ARN/secret-uri depending on backend
          - public_key: the public key content (safe to push to servers)
        """
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / key_name
            cmd = ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path), "-q"]
            if email:
                cmd[3:3] = ["-C", email]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Local keygen failed:\n{result.stderr}")

            private_key_content = key_path.read_text()
            public_key = (Path(tmp) / f"{key_name}.pub").read_text().strip()

            reference = self.key_storage.store_private_key(key_name, private_key_content)
            # tmp dir is deleted here automatically — no plaintext copy left behind

        return {
            "private_key_reference": reference,
            "public_key": public_key,
        }

    def retrieve_private_key(self, reference: str) -> str:
        """Used later (e.g. by a deploy step) to actually fetch the key for use."""
        return self.key_storage.retrieve_private_key(reference)