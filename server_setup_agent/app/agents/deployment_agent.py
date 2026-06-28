from typing import Dict, Any, Literal
from langchain_core.tools import StructuredTool
from langchain_core.messages import SystemMessage, AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, MessagesState, END
from loguru import logger

from app.services.llm_service import get_llm
from app.executors.executor_factory import ExecutorFactory

from app.tools.package_tool import PackageTool
from app.tools.nginx_tool import NginxTool
from app.tools.systemd_tool import SystemdTool
from app.tools.docker_tool import DockerTool
from app.tools.linux_tool import LinuxTool
from app.tools.pm2_tool import PM2Tool

from app.prompts.deployment_prompt import DEPLOYMENT_PROMPT


class DeploymentAgent:
    """
    Agent responsible for orchestrating application deployments.
    Credentials are held securely by the Executor and NEVER sent to the LLM.
    """

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}

        self.executor = ExecutorFactory.get_executor(executor_type, **executor_config)

        pkg_tool    = PackageTool(self.executor)
        nginx_tool  = NginxTool(self.executor)
        sys_tool    = SystemdTool(self.executor)
        docker_tool = DockerTool(self.executor)
        linux_tool  = LinuxTool(self.executor)
        pm2_tool    = PM2Tool(self.executor)

        self.tools = [
            # ── Package management ─────────────────────────────────────────
            StructuredTool.from_function(func=pkg_tool.install, name="install_package",
                description="Installs an apt package. Args: package_name."),

            # ── Nginx ──────────────────────────────────────────────────────
            StructuredTool.from_function(func=nginx_tool.install, name="install_nginx",
                description="Installs Nginx."),
            StructuredTool.from_function(
                func=nginx_tool.generate_and_save_config,
                name="nginx_deploy",
                description="Generates+saves Nginx config. Args: framework(react/vite/static/fastapi/nodejs/nextjs/flask/django), domain, app_name, app_path(static only), port(proxy only,int)."),
            StructuredTool.from_function(func=nginx_tool.test_config,  name="nginx_test",
                description="Tests Nginx config. Run before enabling."),
            StructuredTool.from_function(func=nginx_tool.enable_site,  name="nginx_enable",
                description="Enables Nginx site. Args: app_name."),
            StructuredTool.from_function(func=nginx_tool.reload_nginx, name="nginx_reload",
                description="Reloads Nginx."),

            # ── Systemd ────────────────────────────────────────────────────
            StructuredTool.from_function(
                func=sys_tool.create_service_file, name="systemd_create",
                description="Creates systemd service. Args: service_name, exec_start, working_directory."),
            StructuredTool.from_function(func=sys_tool.start_service,  name="systemd_start",
                description="Starts systemd service. Args: service_name."),
            StructuredTool.from_function(func=sys_tool.enable_service, name="systemd_enable",
                description="Enables systemd service on boot. Args: service_name."),

            # ── PM2 ────────────────────────────────────────────────────────
            StructuredTool.from_function(func=pm2_tool.install, name="pm2_install",
                description="Installs PM2 globally."),
            StructuredTool.from_function(
                func=pm2_tool.start, name="pm2_start",
                description="Starts app with PM2. Args: app_name, script, working_directory."),
            StructuredTool.from_function(func=pm2_tool.save, name="pm2_save",
                description="Saves PM2 list for reboot persistence."),

            # ── Docker ─────────────────────────────────────────────────────
            StructuredTool.from_function(func=docker_tool.install,       name="docker_install",
                description="Installs Docker."),
            StructuredTool.from_function(func=docker_tool.start_service, name="docker_start",
                description="Starts Docker service."),

            # ── Shell ──────────────────────────────────────────────────────
            StructuredTool.from_function(
                func=linux_tool.run_custom_command, name="run",
                description="Runs a shell command (git clone, npm install, pip install, mkdir, etc). Raises on failure."),
            StructuredTool.from_function(
                func=linux_tool.inspect_path, name="inspect",
                description="Runs read-only command (ls, test -f) without raising. Use for verification."),
        ]

        self.llm = get_llm()
        self._build_graph()

    def _build_graph(self):
        """
        Builds the LangGraph ReAct loop with ToolNode(handle_tool_errors=True).

        The system prompt is injected ONCE at graph entry — not on every loop
        iteration — so Groq never sees duplicate system messages mid-conversation.
        """
        # Force the LLM to call tools one at a time.
        # parallel_tool_calls=False is the only reliable way to prevent the model
        # from batching all steps in a single response before seeing any results.
        llm_with_tools = self.llm.bind_tools(
            self.tools,
            parallel_tool_calls=False,
        )

        tool_node = ToolNode(
            self.tools,
            # Catch RuntimeError from tools and return it as a ToolMessage so
            # the LLM can read the error and decide how to recover.
            handle_tool_errors=True,
        )

        def call_model(state: MessagesState):
            """LLM node — injects system prompt, trims history, stops if done."""
            messages = state["messages"]

            # Stop immediately if deployment already completed
            for msg in messages[-3:]:
                if "DEPLOYMENT COMPLETE" in str(getattr(msg, "content", "")):
                    # Return a final AI message with no tool calls to end the loop
                    from langchain_core.messages import AIMessage as AI
                    return {"messages": [AI(content="Deployment completed successfully.")]}

            if not isinstance(messages[0], SystemMessage):
                messages = [SystemMessage(content=DEPLOYMENT_PROMPT)] + list(messages)

            # Trim history to keep token usage under 6000 TPM limit:
            # Always keep: system prompt + first user message + last 10 messages
            MAX_RECENT = 10
            if len(messages) > MAX_RECENT + 2:
                messages = messages[:2] + messages[-(MAX_RECENT):]

            # Truncate tool results — only last 3 lines, max 150 chars
            from langchain_core.messages import ToolMessage
            trimmed = []
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    content = str(msg.content)
                    # Keep only last 3 non-empty lines
                    lines = [l for l in content.splitlines() if l.strip()]
                    short = "\n".join(lines[-3:]) if lines else content
                    if len(short) > 150:
                        short = short[-150:]
                    trimmed.append(ToolMessage(
                        content=short,
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                    ))
                else:
                    trimmed.append(msg)
            messages = trimmed

            response = llm_with_tools.invoke(messages)
            return {"messages": [response]}

        def should_continue(state: MessagesState) -> Literal["tools", "__end__"]:
            last = state["messages"][-1]
            # Stop if deployment is complete
            if hasattr(last, "content") and "DEPLOYMENT COMPLETE" in str(last.content):
                return END
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                return "tools"
            return END

        graph = StateGraph(MessagesState)
        graph.add_node("agent", call_model)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue)
        graph.add_edge("tools", "agent")
        self.agent_executor = graph.compile()

    def execute_task(self, query: str) -> str:
        """
        Runs the deployment request through the LangGraph agent.
        Logs every tool call, result, and LLM response time.
        """
        import time
        inputs = {"messages": [("user", query)]}
        final_state = None
        llm_call_start = None

        for state in self.agent_executor.stream(inputs, stream_mode="values"):
            final_state = state
            messages = state.get("messages", [])
            if not messages:
                continue
            last = messages[-1]

            # AIMessage with tool calls — LLM responded
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                if llm_call_start:
                    elapsed = time.time() - llm_call_start
                    logger.info(f"[GROQ] Response received in {elapsed:.1f}s")
                for tc in last.tool_calls:
                    args_preview = str(tc.get("args", {}))[:150]
                    logger.info(f"[TOOL CALL]   {tc['name']}  {args_preview}")
                llm_call_start = None

            # ToolMessage — tool executed, now waiting for LLM again
            elif hasattr(last, "name") and last.name and hasattr(last, "content"):
                content_preview = str(last.content)[:300]
                logger.info(f"[TOOL RESULT] {last.name}: {content_preview}")
                llm_call_start = time.time()
                logger.debug(f"[GROQ] Waiting for LLM response...")

            # AIMessage without tool calls — final response
            elif isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
                if llm_call_start:
                    elapsed = time.time() - llm_call_start
                    logger.info(f"[GROQ] Final response in {elapsed:.1f}s")

        if final_state is None:
            return "Agent produced no output."

        return final_state["messages"][-1].content
