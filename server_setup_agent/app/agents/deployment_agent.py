from typing import Dict, Any
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from app.services.llm_service import get_llm
from app.executors.executor_factory import ExecutorFactory

from app.tools.package_tool import PackageTool
from app.tools.nginx_tool import NginxTool
from app.tools.systemd_tool import SystemdTool
from app.tools.docker_tool import DockerTool
from app.tools.linux_tool import LinuxTool

class DeploymentAgent:
    """
    Agent responsible for orchestrating application deployments (React, FastAPI, Docker, Nginx).
    Credentials are held securely by the Executor and NEVER sent to the LLM.
    """

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}
            
        self.executor = ExecutorFactory.get_executor(executor_type, **executor_config)
        
        # Instantiate required tools
        pkg_tool = PackageTool(self.executor)
        nginx_tool = NginxTool(self.executor)
        sys_tool = SystemdTool(self.executor)
        docker_tool = DockerTool(self.executor)
        linux_tool = LinuxTool(self.executor) 
        
        # Wrap methods as LangChain Tools
        self.tools = [
            StructuredTool.from_function(func=pkg_tool.install, name="install_package", description="Installs an apt package (e.g., git, nodejs)."),
            StructuredTool.from_function(func=nginx_tool.install, name="install_nginx", description="Installs Nginx."),
            StructuredTool.from_function(func=nginx_tool.start, name="start_nginx", description="Starts and enables Nginx."),
            StructuredTool.from_function(func=nginx_tool.restart, name="restart_nginx", description="Restarts Nginx."),
            StructuredTool.from_function(func=nginx_tool.test_config, name="test_nginx_config", description="Tests Nginx configuration."),
            StructuredTool.from_function(func=sys_tool.start_service, name="start_service", description="Starts a systemd service."),
            StructuredTool.from_function(func=sys_tool.restart_service, name="restart_service", description="Restarts a systemd service."),
            StructuredTool.from_function(func=sys_tool.check_status, name="check_service_status", description="Checks status of a systemd service."),
            StructuredTool.from_function(func=docker_tool.install, name="install_docker", description="Installs Docker."),
            StructuredTool.from_function(func=docker_tool.start_service, name="start_docker_service", description="Starts Docker service."),
            StructuredTool.from_function(func=linux_tool.run_custom_command, name="run_custom_command", description="Runs a generic shell command. Use this for operations like 'git clone', file copying, or echoing config blocks.")
        ]
        
        self.llm = get_llm()
        system_message = (
            "You are an expert DevOps engineer specializing in application deployments. "
            "Use your tools to deploy the requested applications. If you need to clone repositories "
            "or write configuration files (like nginx blocks), use the 'run_custom_command' tool."
        )
        
        self.agent_executor = create_react_agent(self.llm, self.tools, state_modifier=system_message)

    def execute_task(self, query: str) -> str:
        """
        Runs the deployment request through the LangGraph agent.
        """
        inputs = {"messages": [("user", query)]}
        result = self.agent_executor.invoke(inputs)
        return result["messages"][-1].content
