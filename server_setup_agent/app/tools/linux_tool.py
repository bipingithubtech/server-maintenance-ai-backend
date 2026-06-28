import re
from app.executors.base_executor import BaseExecutor


class LinuxTool:
    """
    Tool for generic Linux system commands like file manipulation and permissions.
    """

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def run_custom_command(self, command: str) -> str:
        """
        Runs a shell command expected to succeed and raises if it fails.
        Auto-recovers from common errors.
        Special case: skips 'npm run build' if dist/ already has files.
        """
        # Skip build if dist already exists and has content
        if "npm run build" in command:
            prefix_match = __import__('re').search(r'--prefix\s+(\S+)', command)
            if prefix_match:
                app_path = prefix_match.group(1)
                check_code, check_out, _ = self.executor.execute(
                    f"ls {app_path}/dist 2>/dev/null | head -1"
                )
                if check_code == 0 and check_out.strip():
                    return f"Build skipped — dist/ already exists at {app_path}/dist with files: {check_out.strip()[:100]}"
        exit_code, stdout, stderr = self.executor.execute(command)

        # Auto-recover: directory already exists — switch clone to pull
        if exit_code != 0 and "already exists and is not an empty directory" in stderr:
            match = re.search(r'git clone\s+\S+\s+(\S+)', command)
            if match:
                target_path = match.group(1)
                pull_cmd = (
                    'GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy -o StrictHostKeyChecking=no" '
                    f'git -C {target_path} pull'
                )
                exit_code, stdout, stderr = self.executor.execute(pull_cmd)

        # Auto-recover: npm ERESOLVE peer dependency conflict — retry with --legacy-peer-deps
        if exit_code != 0 and "ERESOLVE" in stderr and "npm" in command:
            legacy_cmd = command.replace("npm install", "npm install --legacy-peer-deps")
            if legacy_cmd != command:
                exit_code, stdout, stderr = self.executor.execute(legacy_cmd)

        # Auto-recover: npm ENOTEMPTY — corrupted node_modules, clean and retry
        if exit_code != 0 and "ENOTEMPTY" in stderr and "node_modules" in stderr:
            prefix_match = __import__('re').search(r'--prefix\s+(\S+)', command)
            app_path = prefix_match.group(1) if prefix_match else None
            if app_path:
                self.executor.execute(f"rm -rf {app_path}/node_modules/.cache")
                self.executor.execute(f"rm -rf {app_path}/node_modules/.glob-*")
                exit_code, stdout, stderr = self.executor.execute(command)
        if exit_code != 0 and "could not create work tree dir" in stderr:
            match = re.search(r"could not create work tree dir '([^']+)'", stderr)
            if match:
                target_path = match.group(1)
                self.executor.execute(
                    f"sudo mkdir -p {target_path} && "
                    f"sudo chown -R $(whoami):$(id -gn) {target_path}"
                )
                exit_code, stdout, stderr = self.executor.execute(command)

        # Auto-recover: generic permission denied (EACCES from npm/pip) — chown and retry
        if exit_code != 0 and ("EACCES" in stderr or "permission denied" in stderr.lower()):
            path = self._extract_path_from_error(stderr)
            if path:
                self.executor.execute(f"sudo chown -R $(whoami):$(id -gn) {path}")
                exit_code, stdout, stderr = self.executor.execute(command)

        if exit_code != 0:
            raise RuntimeError(f"Command failed:\nSTDOUT: {stdout}\nSTDERR: {stderr}")

        return stdout

    def _extract_path_from_error(self, stderr: str) -> str:
        """
        Extracts the file/directory path from a permission-denied error message.
        Handles npm, pip, and generic shell error formats.
        """
        # npm-style: "npm error path /some/path"
        match = re.search(r"npm error path (.+)", stderr)
        if match:
            path = match.group(1).strip()
            return "/".join(path.split("/")[:-1]) or path

        # Generic: "open '/some/path'"
        match = re.search(r"open '(.+)'", stderr)
        if match:
            path = match.group(1).strip()
            return "/".join(path.split("/")[:-1]) or path

        # Generic: "Permission denied: '/some/path'"
        match = re.search(r"[Pp]ermission denied[:\s]+'?([/\w\-_.]+)'?", stderr)
        if match:
            path = match.group(1).strip()
            return "/".join(path.split("/")[:-1]) or path

        return None

    def inspect_path(self, command: str) -> str:
        """
        Runs a read-only inspection command (e.g. 'ls', 'cat', 'test -f') WITHOUT raising
        if the command fails. Use this for checks where failure is a normal, expected
        outcome -- such as checking whether package.json or requirements.txt exists when
        detecting a project's stack. Always returns a result describing success or failure
        so you can reason about what to try next, instead of crashing the task.
        """
        exit_code, out, err = self.executor.execute(command)
        if exit_code != 0:
            return (
                f"COMMAND DID NOT SUCCEED (exit code {exit_code}). "
                "This may simply mean the file/path does not exist, which is normal "
                f"during stack detection. Details: {err.strip() or out.strip() or 'no output'}"
            )
        return out if out else "Command succeeded with no output."

    def get_os_info(self) -> str:
        """Gets information about the Linux distribution from /etc/os-release."""
        exit_code, out, err = self.executor.execute("cat /etc/os-release")
        if exit_code != 0:
            raise RuntimeError(f"Failed to get OS info:\n{err}")
        return out

    def change_permissions(self, path: str, permissions: str) -> str:
        """Changes file or directory permissions (chmod)."""
        exit_code, out, err = self.executor.execute(f"sudo chmod {permissions} {path}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to change permissions for {path}:\n{err}")
        return f"Permissions changed successfully to {permissions} for {path}."

    def change_owner(self, path: str, owner: str, group: str = None) -> str:
        """Changes file or directory ownership (chown)."""
        target = f"{owner}:{group}" if group else owner
        exit_code, out, err = self.executor.execute(f"sudo chown {target} {path}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to change ownership for {path}:\n{err}")
        return f"Ownership changed successfully for {path}."
