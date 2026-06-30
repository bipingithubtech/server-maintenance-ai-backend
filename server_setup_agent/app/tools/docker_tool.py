import shlex
from app.executors.base_executor import BaseExecutor


class DockerTool:
    """Tool for installing Docker and managing containers."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def install(self) -> str:
        exit_code, out, err = self.executor.execute(
            "curl -fsSL https://get.docker.com | sudo sh"
        )
        if exit_code != 0:
            raise RuntimeError(f"Docker install failed:\n{err}")
        return "Docker installed."

    def start_service(self) -> str:
        exit_code, out, err = self.executor.execute(
            "sudo systemctl enable docker && sudo systemctl start docker"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to start Docker:\n{err}")
        return "Docker service started."

    def container_exists(self, name: str) -> bool:
        code, out, _ = self.executor.execute(
            f"sudo docker ps -a --filter name=^{shlex.quote(name)}$ --format '{{{{.Names}}}}'"
        )
        return out.strip() == name

    def container_running(self, name: str) -> bool:
        code, out, _ = self.executor.execute(
            f"sudo docker ps --filter name=^{shlex.quote(name)}$ --format '{{{{.Names}}}}'"
        )
        return out.strip() == name

    def run_container(
        self,
        name: str,
        image: str,
        ports: dict,
        env: dict = None,
        volumes: dict = None,
        restart_policy: str = "unless-stopped",
    ) -> str:
        """
        Idempotent container runner: if it exists and is running, skip.
        If it exists but stopped, start it. Otherwise create + run it.

        ports: {"host_bind": "container_port"} e.g. {"127.0.0.1:6379": "6379"}
        """
        env = env or {}
        volumes = volumes or {}

        if self.container_running(name):
            return f"Container '{name}' already running — skipped."

        if self.container_exists(name):
            exit_code, out, err = self.executor.execute(f"sudo docker start {shlex.quote(name)}")
            if exit_code != 0:
                raise RuntimeError(f"Failed to start existing container {name}:\n{err}")
            return f"Existing container '{name}' started."

        port_flags = " ".join(f"-p {host}:{container}" for host, container in ports.items())
        env_flags = " ".join(f"-e {shlex.quote(k)}={shlex.quote(v)}" for k, v in env.items())
        volume_flags = " ".join(f"-v {shlex.quote(h)}:{shlex.quote(c)}" for h, c in volumes.items())

        cmd = (
            f"sudo docker run -d "
            f"--name {shlex.quote(name)} "
            f"--restart {restart_policy} "
            f"{port_flags} {env_flags} {volume_flags} "
            f"{shlex.quote(image)}"
        )
        exit_code, out, err = self.executor.execute(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to run container {name}:\n{err}")
        return f"Container '{name}' created and running (id: {out.strip()[:12]})."