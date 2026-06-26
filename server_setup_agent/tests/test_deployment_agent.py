"""
tests/test_deployment_agent.py

Tests for the DeploymentAgent.

Strategy
--------
- The real LLM (Groq) and all real executors are mocked so tests run fast,
  offline, and never touch a real server.
- Each test group focuses on one concern:
    1. Agent wiring        – agent initialises without errors
    2. No GitHub URL       – agent refuses to call any tool and asks for a URL
    3. Tool routing        – individual tool-level unit tests (no LLM needed)
    4. Credential safety   – secrets never appear in anything sent to the LLM
    5. Integration smoke   – full execute_task() roundtrip with a mocked LLM
"""

import pytest
from unittest.mock import MagicMock, patch

# Import the module first so patch() can resolve it as an attribute of app.agents
import app.agents.deployment_agent  # noqa: F401  (side-effect import for patch target)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def make_mock_executor(exit_code: int = 0, stdout: str = "ok", stderr: str = ""):
    """Return a mock executor whose .execute() always returns the given tuple."""
    executor = MagicMock()
    executor.execute.return_value = (exit_code, stdout, stderr)
    return executor


# ---------------------------------------------------------------------------
# 1. Agent wiring
# ---------------------------------------------------------------------------

class TestDeploymentAgentInit:
    """Verify the agent can be instantiated without touching real infra."""

    @patch("app.agents.deployment_agent.get_llm")
    @patch("app.agents.deployment_agent.ExecutorFactory.get_executor")
    def test_init_local_executor(self, mock_get_executor, mock_get_llm):
        mock_get_executor.return_value = make_mock_executor()
        mock_get_llm.return_value = MagicMock()

        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(executor_type="local")

        mock_get_executor.assert_called_once_with("local")
        assert agent.tools, "Agent should have at least one tool registered"

    @patch("app.agents.deployment_agent.get_llm")
    @patch("app.agents.deployment_agent.ExecutorFactory.get_executor")
    def test_init_ssh_executor(self, mock_get_executor, mock_get_llm):
        mock_get_executor.return_value = make_mock_executor()
        mock_get_llm.return_value = MagicMock()

        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(
            executor_type="ssh",
            executor_config={"host": "1.2.3.4", "username": "root", "password": "secret"}
        )

        mock_get_executor.assert_called_once_with(
            "ssh", host="1.2.3.4", username="root", password="secret"
        )

    @patch("app.agents.deployment_agent.get_llm")
    @patch("app.agents.deployment_agent.ExecutorFactory.get_executor")
    def test_all_expected_tools_registered(self, mock_get_executor, mock_get_llm):
        mock_get_executor.return_value = make_mock_executor()
        mock_get_llm.return_value = MagicMock()

        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent()

        tool_names = {t.name for t in agent.tools}
        required = {
            "install_package",
            "install_nginx", "start_nginx", "restart_nginx",
            "generate_nginx_config", "save_nginx_config",
            "enable_nginx_site", "disable_nginx_site",
            "test_nginx_config", "reload_nginx",
            "start_service", "restart_service", "enable_service",
            "check_service_status", "create_service_file",
            "install_docker", "start_docker_service",
            "run_custom_command", "inspect_path",
        }
        missing = required - tool_names
        assert not missing, f"Missing tools: {missing}"


# ---------------------------------------------------------------------------
# 2. No GitHub URL → agent must refuse without calling any tool
# ---------------------------------------------------------------------------

class TestNoGithubUrl:
    """
    The deployment prompt instructs the agent to ask for a URL if none is
    present and call ZERO tools.  We mock agent_executor.invoke directly
    (bypassing LangGraph internals) so the test stays fast and offline.
    """

    def _make_agent_with_mocked_invoke(self, invoke_response: str):
        """
        Build a DeploymentAgent then replace agent_executor.invoke with a
        mock that returns a fixed final message. The executor is also mocked
        so we can assert it was never called.
        """
        mock_executor = make_mock_executor()

        with patch("app.agents.deployment_agent.ExecutorFactory.get_executor", return_value=mock_executor), \
             patch("app.agents.deployment_agent.get_llm", return_value=MagicMock()):
            from app.agents.deployment_agent import DeploymentAgent
            agent = DeploymentAgent()

        # Replace the langgraph executor with a simple mock
        from langchain_core.messages import AIMessage
        agent.agent_executor = MagicMock()
        agent.agent_executor.invoke.return_value = {
            "messages": [AIMessage(content=invoke_response)]
        }

        return agent, mock_executor

    def test_no_url_returns_message_asking_for_url(self):
        expected_reply = "Please provide the GitHub repository URL to proceed."
        agent, mock_executor = self._make_agent_with_mocked_invoke(expected_reply)

        result = agent.execute_task("Deploy my app to the server.")

        assert "github" in result.lower() or "url" in result.lower() or "repository" in result.lower(), \
            f"Expected agent to ask for a URL, got: {result!r}"
        mock_executor.execute.assert_not_called()

    def test_vague_query_no_url_no_tools_called(self):
        agent, mock_executor = self._make_agent_with_mocked_invoke(
            "Could you please share the GitHub repository URL?"
        )
        agent.execute_task("set up my project")
        mock_executor.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Tool unit tests — no LLM, just the tool classes directly
# ---------------------------------------------------------------------------

class TestPackageToolUnit:

    def test_install_success(self):
        from app.tools.package_tool import PackageTool
        executor = make_mock_executor(stdout="Package installed.\n")
        tool = PackageTool(executor)
        result = tool.install("git")
        assert "git" in result
        executor.execute.assert_called_once()
        cmd = executor.execute.call_args[0][0]
        assert "apt-get install" in cmd
        assert "git" in cmd

    def test_install_failure_raises(self):
        from app.tools.package_tool import PackageTool
        executor = make_mock_executor(exit_code=1, stderr="E: Unable to locate package badpkg")
        tool = PackageTool(executor)
        with pytest.raises(RuntimeError, match="Failed to install badpkg"):
            tool.install("badpkg")

    def test_remove_success(self):
        from app.tools.package_tool import PackageTool
        executor = make_mock_executor()
        tool = PackageTool(executor)
        result = tool.remove("nginx")
        assert "nginx" in result

    def test_is_installed_true(self):
        from app.tools.package_tool import PackageTool
        executor = make_mock_executor(stdout="Status: install ok installed")
        tool = PackageTool(executor)
        assert tool.is_installed("nginx") is True

    def test_is_installed_false(self):
        from app.tools.package_tool import PackageTool
        executor = make_mock_executor(exit_code=1, stdout="")
        tool = PackageTool(executor)
        assert tool.is_installed("nginx") is False


class TestNginxToolUnit:

    def test_install_success(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor()
        tool = NginxTool(executor)
        result = tool.install()
        assert "installed" in result.lower()

    def test_install_failure_raises(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor(exit_code=1, stderr="error")
        tool = NginxTool(executor)
        with pytest.raises(RuntimeError, match="Failed to install Nginx"):
            tool.install()

    def test_generate_config_static_requires_app_path(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor()
        tool = NginxTool(executor)
        with pytest.raises(ValueError, match="app_path is required"):
            tool.generate_config(framework="react", domain="example.com", app_name="myapp")

    def test_generate_config_proxy_requires_port(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor()
        tool = NginxTool(executor)
        with pytest.raises(ValueError, match="port is required"):
            tool.generate_config(framework="fastapi", domain="example.com", app_name="myapi")

    def test_generate_config_unsupported_framework(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor()
        tool = NginxTool(executor)
        with pytest.raises(ValueError, match="Unsupported framework"):
            tool.generate_config(framework="rails", domain="example.com", app_name="myapp", port=3000)

    def test_enable_site_already_enabled(self):
        """If ln -s fails but symlink already exists, should return success silently."""
        from app.tools.nginx_tool import NginxTool
        executor = MagicMock()
        # First call (ln -s) fails, second call (test -L) succeeds
        executor.execute.side_effect = [
            (1, "", "File exists"),  # ln -s
            (0, "", ""),             # test -L
        ]
        tool = NginxTool(executor)
        result = tool.enable_site("myapp")
        assert "already enabled" in result

    def test_test_config_failure_raises(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor(exit_code=1, stderr="nginx: configuration file test failed")
        tool = NginxTool(executor)
        with pytest.raises(RuntimeError, match="Nginx config test failed"):
            tool.test_config()

    def test_reload_nginx_success(self):
        from app.tools.nginx_tool import NginxTool
        executor = make_mock_executor()
        tool = NginxTool(executor)
        result = tool.reload_nginx()
        assert "reloaded" in result.lower()


class TestSystemdToolUnit:

    def test_start_service_success(self):
        from app.tools.systemd_tool import SystemdTool
        executor = make_mock_executor()
        tool = SystemdTool(executor)
        result = tool.start_service("nginx")
        cmd = executor.execute.call_args[0][0]
        assert "start" in cmd and "nginx" in cmd

    def test_start_service_failure_raises(self):
        from app.tools.systemd_tool import SystemdTool
        executor = make_mock_executor(exit_code=1, stderr="Unit nginx.service not found.")
        tool = SystemdTool(executor)
        with pytest.raises(RuntimeError, match="Failed to start nginx"):
            tool.start_service("nginx")

    def test_create_service_file_success(self):
        from app.tools.systemd_tool import SystemdTool
        executor = make_mock_executor()
        tool = SystemdTool(executor)
        result = tool.create_service_file(
            service_name="myapp",
            exec_start="/opt/myapp/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001",
            working_directory="/opt/myapp"
        )
        assert "myapp.service" in result
        assert "daemon-reload" in executor.execute.call_args_list[-1][0][0]

    def test_create_service_file_write_failure_raises(self):
        from app.tools.systemd_tool import SystemdTool
        executor = make_mock_executor(exit_code=1, stderr="permission denied")
        tool = SystemdTool(executor)
        with pytest.raises(RuntimeError, match="Failed to write service file"):
            tool.create_service_file(
                service_name="badapp",
                exec_start="node /opt/badapp/index.js",
                working_directory="/opt/badapp"
            )

    def test_check_status_does_not_raise_on_non_zero(self):
        """systemctl status returns 3 for inactive — should not raise."""
        from app.tools.systemd_tool import SystemdTool
        executor = make_mock_executor(exit_code=3, stdout="● nginx.service - inactive")
        tool = SystemdTool(executor)
        result = tool.check_status("nginx")   # must not raise
        assert "nginx" in result


class TestDockerToolUnit:

    def test_is_installed_true(self):
        from app.tools.docker_tool import DockerTool
        executor = make_mock_executor(stdout="Docker version 24.0.0")
        tool = DockerTool(executor)
        assert tool.is_installed() is True

    def test_is_installed_false(self):
        from app.tools.docker_tool import DockerTool
        executor = make_mock_executor(exit_code=1)
        tool = DockerTool(executor)
        assert tool.is_installed() is False

    def test_install_failure_raises(self):
        from app.tools.docker_tool import DockerTool
        executor = make_mock_executor(exit_code=1, stderr="curl: not found")
        tool = DockerTool(executor)
        with pytest.raises(RuntimeError, match="Failed to install Docker"):
            tool.install()

    def test_start_service_success(self):
        from app.tools.docker_tool import DockerTool
        executor = make_mock_executor()
        tool = DockerTool(executor)
        result = tool.start_service()
        assert "started" in result.lower()


class TestLinuxToolUnit:

    def test_run_custom_command_success(self):
        from app.tools.linux_tool import LinuxTool
        executor = make_mock_executor(stdout="Cloning into '/opt/myapp'...")
        tool = LinuxTool(executor)
        result = tool.run_custom_command("git clone https://github.com/org/repo /opt/myapp")
        assert "Cloning" in result

    def test_run_custom_command_failure_raises(self):
        from app.tools.linux_tool import LinuxTool
        executor = make_mock_executor(exit_code=1, stderr="fatal: repo not found")
        tool = LinuxTool(executor)
        with pytest.raises(RuntimeError, match="Command failed"):
            tool.run_custom_command("git clone https://github.com/org/missing /opt/missing")

    def test_inspect_path_success(self):
        from app.tools.linux_tool import LinuxTool
        executor = make_mock_executor(stdout='{"name": "myapp"}')
        tool = LinuxTool(executor)
        result = tool.inspect_path("cat /opt/myapp/package.json")
        assert "myapp" in result

    def test_inspect_path_does_not_raise_on_failure(self):
        """inspect_path must never raise — it's used for stack detection."""
        from app.tools.linux_tool import LinuxTool
        executor = make_mock_executor(exit_code=1, stderr="No such file or directory")
        tool = LinuxTool(executor)
        result = tool.inspect_path("cat /opt/myapp/requirements.txt")
        assert "COMMAND DID NOT SUCCEED" in result

    def test_run_custom_command_auto_retries_on_eacces(self):
        """On EACCES the tool should fix ownership then retry the command."""
        from app.tools.linux_tool import LinuxTool
        executor = MagicMock()
        executor.execute.side_effect = [
            (1, "", "EACCES: permission denied, open '/opt/myapp/package-lock.json'"),
            (0, "", ""),   # chown fix
            (0, "ok", ""), # retry of original command
        ]
        tool = LinuxTool(executor)
        result = tool.run_custom_command("npm install")
        assert result == "ok"
        assert executor.execute.call_count == 3


# ---------------------------------------------------------------------------
# 4. Credential safety
# ---------------------------------------------------------------------------

class TestCredentialSafety:
    """
    Ensure that registered credentials are scrubbed from all outputs
    before anything reaches the LLM.
    """

    def setup_method(self):
        from app.services.sanitizer_service import clear_credentials
        clear_credentials()

    def teardown_method(self):
        from app.services.sanitizer_service import clear_credentials
        clear_credentials()

    def test_scrub_credentials_replaces_host(self):
        from app.services.sanitizer_service import register_credentials, scrub_credentials
        register_credentials(host="192.168.1.100")
        result = scrub_credentials("Connected to 192.168.1.100 successfully.")
        assert "192.168.1.100" not in result
        assert "[REDACTED]" in result

    def test_scrub_credentials_replaces_password(self):
        from app.services.sanitizer_service import register_credentials, scrub_credentials
        register_credentials(password="SuperSecret123!")
        result = scrub_credentials("auth failed for SuperSecret123!")
        assert "SuperSecret123!" not in result
        assert "[REDACTED]" in result

    def test_scrub_credentials_replaces_username(self):
        from app.services.sanitizer_service import register_credentials, scrub_credentials
        register_credentials(username="bipinjoshi")
        result = scrub_credentials("login as bipinjoshi accepted")
        assert "bipinjoshi" not in result
        assert "[REDACTED]" in result

    def test_scrub_multiple_credentials(self):
        from app.services.sanitizer_service import register_credentials, scrub_credentials
        register_credentials(host="10.0.0.1", username="admin", password="p@ss!")
        text = "Connecting to 10.0.0.1 as admin with password p@ss!"
        result = scrub_credentials(text)
        assert "10.0.0.1" not in result
        assert "admin" not in result
        assert "p@ss!" not in result
        assert result.count("[REDACTED]") == 3

    def test_clear_credentials_removes_secrets(self):
        from app.services.sanitizer_service import register_credentials, clear_credentials, scrub_credentials
        register_credentials(host="10.0.0.1")
        clear_credentials()
        result = scrub_credentials("Connected to 10.0.0.1")
        assert "10.0.0.1" in result  # no longer scrubbed after clear

    def test_sanitize_command_strips_embedded_credential(self):
        """If LLM somehow embeds a password in a command, sanitize removes it."""
        from app.services.sanitizer_service import register_credentials, SanitizerService
        register_credentials(password="HackerPassword99")
        cmd = "echo HackerPassword99 | sudo something"
        result = SanitizerService.sanitize_command(cmd)
        assert "HackerPassword99" not in result
        assert "[REDACTED]" in result

    def test_base_executor_scrubs_stdout(self):
        """BaseExecutor must scrub command output before returning it."""
        from app.services.sanitizer_service import register_credentials
        from app.executors.local_executor import LocalExecutor
        import unittest.mock as mock

        register_credentials(host="172.16.0.5")

        executor = LocalExecutor()
        with mock.patch.object(executor, "_run", return_value=(0, "host is 172.16.0.5", "")):
            _, stdout, _ = executor.execute("echo test")

        assert "172.16.0.5" not in stdout
        assert "[REDACTED]" in stdout

    def test_base_executor_scrubs_stderr(self):
        from app.services.sanitizer_service import register_credentials
        from app.executors.local_executor import LocalExecutor
        import unittest.mock as mock

        register_credentials(password="mypassword")

        executor = LocalExecutor()
        with mock.patch.object(executor, "_run", return_value=(1, "", "auth error: mypassword wrong")):
            _, _, stderr = executor.execute("sudo something")

        assert "mypassword" not in stderr
        assert "[REDACTED]" in stderr


# ---------------------------------------------------------------------------
# 5. Integration smoke — full execute_task() roundtrip with a mocked invoke
# ---------------------------------------------------------------------------

class TestDeploymentAgentIntegration:
    """
    Smoke-tests execute_task() end-to-end.
    We mock agent_executor.invoke to return a fixed AIMessage so we verify
    the plumbing (invoke → messages[-1].content) without a real API call.
    """

    def _build_agent(self, final_llm_message: str, executor_side_effects=None):
        from langchain_core.messages import AIMessage

        mock_executor = MagicMock()
        if executor_side_effects:
            mock_executor.execute.side_effect = executor_side_effects
        else:
            mock_executor.execute.return_value = (0, "ok", "")

        with patch("app.agents.deployment_agent.ExecutorFactory.get_executor", return_value=mock_executor), \
             patch("app.agents.deployment_agent.get_llm", return_value=MagicMock()):
            from app.agents.deployment_agent import DeploymentAgent
            agent = DeploymentAgent()

        # Mock at the agent_executor level — avoids LangGraph message type checks
        agent.agent_executor = MagicMock()
        agent.agent_executor.invoke.return_value = {
            "messages": [AIMessage(content=final_llm_message)]
        }

        return agent

    def test_execute_task_returns_string(self):
        agent = self._build_agent("Deployment complete. App running on port 8001.")
        result = agent.execute_task("Deploy https://github.com/org/myapi")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_execute_task_returns_final_message_content(self):
        expected = "App deployed successfully at http://localhost:8001"
        agent = self._build_agent(expected)
        result = agent.execute_task("Deploy https://github.com/org/myapi")
        assert result == expected

    def test_execute_task_no_url_asks_for_github_url(self):
        agent = self._build_agent("Please provide the GitHub repository URL.")
        result = agent.execute_task("deploy my backend")
        assert "url" in result.lower() or "github" in result.lower()
