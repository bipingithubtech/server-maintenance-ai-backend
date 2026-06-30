"""
Pluggable key storage backend for SSH private keys and infra credentials.
Default: local disk (locked down). Swap to Vault/AWS Secrets Manager via
the KEY_STORAGE_BACKEND env var without touching any calling code.
"""

import os
import json
from abc import ABC, abstractmethod
from pathlib import Path


class KeyStorageBackend(ABC):
    @abstractmethod
    def store_private_key(self, key_id: str, private_key_content: str) -> str:
        """Stores the secret, returns a reference (path, ARN, secret path, etc.)."""
        ...

    @abstractmethod
    def retrieve_private_key(self, reference: str) -> str:
        ...


class LocalDiskKeyStorage(KeyStorageBackend):
    """Default backend — locked-down local disk storage."""

    def __init__(self, base_dir: str = "generated_keys"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ensure_gitignored()

    def _ensure_gitignored(self):
        gitignore = Path(".gitignore")
        entry = f"{self.base_dir}/\n"
        if gitignore.exists():
            content = gitignore.read_text()
            if str(self.base_dir) not in content:
                with gitignore.open("a") as f:
                    f.write(f"\n# Auto-added: never commit generated SSH keys\n{entry}")
        else:
            gitignore.write_text(f"# Auto-added: never commit generated SSH keys\n{entry}")

    def store_private_key(self, key_id: str, private_key_content: str) -> str:
        path = self.base_dir / key_id
        path.write_text(private_key_content)
        os.chmod(path, 0o600)
        return str(path)

    def retrieve_private_key(self, reference: str) -> str:
        return Path(reference).read_text()


class VaultKeyStorage(KeyStorageBackend):
    """HashiCorp Vault backend using the KV v2 secrets engine. Requires: pip install hvac"""

    def __init__(self, vault_addr: str, vault_token: str, mount_path: str = "secret", path_prefix: str = "ssh-keys"):
        import hvac
        self.client = hvac.Client(url=vault_addr, token=vault_token)
        if not self.client.is_authenticated():
            raise RuntimeError("Vault authentication failed — check VAULT_ADDR/VAULT_TOKEN.")
        self.mount_path = mount_path
        self.path_prefix = path_prefix

    def store_private_key(self, key_id: str, private_key_content: str) -> str:
        secret_path = f"{self.path_prefix}/{key_id}"
        self.client.secrets.kv.v2.create_or_update_secret(
            path=secret_path,
            secret={"private_key": private_key_content},
            mount_point=self.mount_path,
        )
        return f"vault://{self.mount_path}/{secret_path}"

    def retrieve_private_key(self, reference: str) -> str:
        path = reference.replace(f"vault://{self.mount_path}/", "")
        result = self.client.secrets.kv.v2.read_secret_version(
            path=path, mount_point=self.mount_path
        )
        return result["data"]["data"]["private_key"]


class AWSSecretsManagerKeyStorage(KeyStorageBackend):
    """AWS Secrets Manager backend. Requires: pip install boto3"""

    def __init__(self, region: str = "us-east-1", prefix: str = "ssh-keys"):
        import boto3
        self.client = boto3.client("secretsmanager", region_name=region)
        self.prefix = prefix

    def store_private_key(self, key_id: str, private_key_content: str) -> str:
        secret_name = f"{self.prefix}/{key_id}"
        try:
            self.client.create_secret(
                Name=secret_name,
                SecretString=json.dumps({"private_key": private_key_content}),
            )
        except self.client.exceptions.ResourceExistsException:
            self.client.put_secret_value(
                SecretId=secret_name,
                SecretString=json.dumps({"private_key": private_key_content}),
            )
        return secret_name

    def retrieve_private_key(self, reference: str) -> str:
        response = self.client.get_secret_value(SecretId=reference)
        data = json.loads(response["SecretString"])
        return data["private_key"]


def get_key_storage_backend() -> KeyStorageBackend:
    """Single place controlling which backend is active, set via env var."""
    backend = os.getenv("KEY_STORAGE_BACKEND", "local").lower()

    if backend == "local":
        return LocalDiskKeyStorage(base_dir=os.getenv("KEY_STORAGE_DIR", "generated_keys"))

    elif backend == "vault":
        return VaultKeyStorage(
            vault_addr=os.environ["VAULT_ADDR"],
            vault_token=os.environ["VAULT_TOKEN"],
            mount_path=os.getenv("VAULT_MOUNT_PATH", "secret"),
        )

    elif backend == "aws":
        return AWSSecretsManagerKeyStorage(
            region=os.getenv("AWS_REGION", "us-east-1"),
        )

    else:
        raise ValueError(f"Unknown KEY_STORAGE_BACKEND: {backend}")