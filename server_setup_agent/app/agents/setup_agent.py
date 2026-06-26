from typing import Dict, Any
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from app.services.llm_service import get_llm
from app.executors.executor_factory import ExecutorFactory

from app.tools.package_tool import PackageTool
from app.tools.firewall_tool import FirewallTool
from app.tools.user_tool import UserTool
from app.tools.security_tool import SecurityTool


class SetupAgent:
    """
    Agent responsible for server initialization using LangGraph.
    Credentials are held securely by the Executor and NEVER sent to the LLM.
    All commands are automatically sanitized and policy-checked by the executor.
    """

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}
            
        # 1. Initialize the executor
        # The executor securely holds SSH credentials. The LLM never sees them.
        self.executor = ExecutorFactory.get_executor(executor_type, **executor_config)
        
        # 2. Instantiate tools
        pkg_tool = PackageTool(self.executor)
        fw_tool = FirewallTool(self.executor)
        usr_tool = UserTool(self.executor)
        sec_tool = SecurityTool(self.executor)
        
        # 3. Wrap methods as LangChain Tools
        # We only expose the functional signatures to the LLM.
        self.tools = [
            StructuredTool.from_function(
                func=pkg_tool.install,
                name="install_package",
                description="Installs an apt package. Provide the package name."
            ),
            StructuredTool.from_function(
                func=pkg_tool.remove,
                name="remove_package",
                description="Removes an apt package. Provide the package name."
            ),
            StructuredTool.from_function(
                func=pkg_tool.update_lists,
                name="update_package_lists",
                description="Updates the apt package lists (apt-get update). Call this before installing."
            ),
            StructuredTool.from_function(
                func=fw_tool.enable,
                name="enable_firewall",
                description="Enables the UFW firewall."
            ),
            StructuredTool.from_function(
                func=fw_tool.allow_port,
                name="allow_firewall_port",
                description="Allows a specific port on the firewall. Args: port (str), protocol (str: tcp/udp)."
            ),
            StructuredTool.from_function(
                func=usr_tool.create_user,
                name="create_user",
                description="Creates a new Linux user account. Args: username (str)."
            ),
            StructuredTool.from_function(
                func=sec_tool.install_fail2ban,
                name="install_fail2ban",
                description="Installs and enables fail2ban for brute-force protection."
            ),
            StructuredTool.from_function(
                func=sec_tool.disable_root_ssh_login,
                name="disable_root_ssh_login",
                description="Disables root SSH login in sshd_config for security."
            )
        ]
        
        # 4. Create the LangGraph agent
        self.llm = get_llm()
        system_message = "You are an expert Linux sysadmin responsible for server setup and configuration. Use your tools to fulfill the user request."
        
        # create_react_agent builds a robust LangGraph state machine loop
        self.agent_executor = create_react_agent(self.llm, self.tools, prompt=system_message)

    def execute_task(self, query: str) -> str:
        """
        Runs the user query through the LangGraph agent.
        """
        inputs = {"messages": [("user", query)]}
        result = self.agent_executor.invoke(inputs)
        
        # The final message in the state contains the LLM's final response
        return result["messages"][-1].content
