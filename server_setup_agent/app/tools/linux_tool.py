import re
from loguru import logger
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

        # Auto-recover: directory already exists — switch clone to plain git pull
        if exit_code != 0 and "already exists and is not an empty directory" in stderr:
            # Always take the last whitespace-separated token as the target path
            # e.g. git clone https://$(cat ~/.github_token)@github.com/Org/repo.git /opt/myapp
            #                                                                         ^^^^^^^^^^^
            tokens = command.strip().split()
            target_path = tokens[-1] if tokens else None
            if target_path and target_path.startswith("/"):
                pull_cmd = f"git -C {target_path} pull"
                exit_code, stdout, stderr = self.executor.execute(pull_cmd)

        # Auto-recover: pdfjs-dist canvas.node webpack error — patch next.config.mjs
        if exit_code != 0 and "canvas.node" in stderr and "Module parse failed" in stderr:
            prefix_match = re.search(r'--prefix\s+(\S+)', command)
            app_path = prefix_match.group(1) if prefix_match else None
            if app_path:
                for config_file in ["next.config.mjs", "next.config.js", "next.config.ts"]:
                    check_code, _, _ = self.executor.execute(f"test -f {app_path}/{config_file}")
                    if check_code == 0:
                        _, config_content, _ = self.executor.execute(f"cat {app_path}/{config_file}")
                        # Only patch if canvas alias not already present
                        if "canvas: false" not in config_content or "resolve.alias" not in config_content:
                            # Inject alias line after the webpack function opening
                            patched = config_content.replace(
                                "config.resolve.fallback = {",
                                "config.resolve.alias = { ...config.resolve.alias, canvas: false };\n    config.resolve.fallback = {",
                                1  # only first occurrence
                            )
                            import base64 as _b64
                            encoded = _b64.b64encode(patched.encode()).decode()
                            write_cmd = f"echo '{encoded}' | base64 --decode > {app_path}/{config_file}"
                            self.executor.execute(write_cmd)
                            logger.info(f"Auto-patched {config_file} to fix canvas.node webpack error")
                        exit_code, stdout, stderr = self.executor.execute(command)
                        break
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
            # npm writes ALL output (including warnings) to stderr.
            # If every non-empty stderr line is just "npm warn", it's not a real error.
            if "npm" in command:
                real_errors = [
                    l for l in stderr.splitlines()
                    if l.strip() and not l.strip().startswith("npm warn")
                    and not l.strip().startswith("npm notice")
                ]
                if not real_errors:
                    logger.info(f"  npm completed with warnings only — treating as success")
                    return stdout or "npm completed with deprecation warnings (not errors)."

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
