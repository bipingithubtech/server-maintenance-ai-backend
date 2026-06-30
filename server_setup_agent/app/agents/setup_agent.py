from typing import Dict, Any, List
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent
from loguru import logger

from app.services.llm_service import get_llm
from app.executors.executor_factory import ExecutorFactory

from app.tools.package_tool import PackageTool
from app.tools.firewall_tool import FirewallTool
from app.tools.user_tool import UserTool
from app.tools.security_tool import SecurityTool
from app.tools.nginx_tool import NginxTool
from app.tools.ssh_tool import SSHTool
from app.tools.systemd_tool import SystemdTool
from app.tools.linux_tool import LinuxTool
from app.tools.monitoring_tool import MonitoringTool
from app.tools.log_tool import LogTool
from app.tools.docker_tool import DockerTool
from app.tools.pm2_tool import PM2Tool

# ── Per-batch system prompts ─────────────────────────────────────────────────
BATCH_PROMPTS = {
    "package":  "You are a Linux sysadmin. Call update_package_lists once and then stop. Do NOT install any packages.",
    "security": "You are a Linux sysadmin. Install fail2ban and disable root SSH login. Report what you did.",
    "firewall": "You are a Linux sysadmin. Enable UFW and allow ports 22, 80, 443. Report what you did.",
    "user":     "You are a Linux sysadmin. Do NOT create, delete, or modify any users or groups. Report 'No user changes needed' and stop.",
    "ssh":      "You are a Linux sysadmin. Do NOT generate any SSH keys. Call get_ssh_public_key once to check if a key exists. Report the result and stop.",
    "nginx":    "You are a Linux sysadmin. Call install_nginx first, then call start_nginx. Do not configure any site. Report what you did.",
    "systemd":  "You are a Linux sysadmin. Do NOT create or start any services. Report 'No systemd changes needed' and stop.",
    "docker":   "You are a Linux sysadmin. Call is_docker_installed first. If not installed, call install_docker, then call start_docker. Report what you did.",
    "pm2":      "You are a Linux sysadmin. Install PM2 globally if not installed. Do not start any app unless specific app name and script are given. Report what you did.",
    "monitor":  "You are a Linux sysadmin. Report CPU, RAM, and disk usage.",
    "log":      "You are a Linux sysadmin. Fetch syslog or journalctl output as requested. Report what you found.",
}

# ── Keyword → batch routing ───────────────────────────────────────────────────
_ROUTES = [
    (["nginx", "site", "proxy", "domain", "vhost", "web"],       "nginx"),
    (["docker", "container", "image"],                            "docker"),
    (["pm2", "node", "nextjs", "nestjs"],                         "pm2"),
    (["systemd", "service", "unit", "daemon"],                    "systemd"),
    (["firewall", "ufw", "port", "allow", "deny"],                "firewall"),
    (["user", "group", "useradd", "usermod"],                     "user"),
    (["ssh", "key", "keypair", "authorized"],                     "ssh"),
    (["security", "fail2ban", "harden", "root login"],            "security"),
    (["log", "journal", "syslog", "tail"],                        "log"),
    (["cpu", "ram", "disk", "memory", "monitor", "usage"],        "monitor"),
    (["install", "remove", "package", "apt", "update"],           "package"),
]

# Full setup pipeline — order matters
FULL_SETUP_PIPELINE = [
    "package",
    "security",
    "firewall",
    "user",
    "ssh",
    "nginx",
    "docker",
    "pm2",
]

FULL_SETUP_KEYWORDS = ["setup", "full setup", "initialise", "initialize", "bootstrap", "provision", "configure server", "set up server"]


class SetupAgent:
    """
    LangGraph-based server setup agent with batched tool execution.

    - For targeted queries (e.g. "install nginx"): routes to the matching
      batch and runs only those tools.
    - For full setup queries (e.g. "set up the server"): runs ALL batches
      sequentially, one at a time, so Groq never sees more than ~10 tools.
    """

    def __init__(self, executor_type: str = "local", executor_config: Dict[str, Any] = None):
        if executor_config is None:
            executor_config = {}

        self.executor = ExecutorFactory.get_executor(executor_type, **executor_config)
        self.llm = get_llm()

        # ── Tool instances ────────────────────────────────────────────────────
        pkg = PackageTool(self.executor)
        fw  = FirewallTool(self.executor)
        usr = UserTool(self.executor)
        sec = SecurityTool(self.executor)
        nx  = NginxTool(self.executor)
        ssh = SSHTool(self.executor)
        svc = SystemdTool(self.executor)
        lx  = LinuxTool(self.executor)
        mon = MonitoringTool(self.executor)
        log = LogTool(self.executor)
        dkr = DockerTool(self.executor)
        pm2 = PM2Tool(self.executor)

        # ── Common tools included in every batch ──────────────────────────────
        self._common = [
            StructuredTool.from_function(
                func=lx.run_custom_command,
                name="run_command",
                description="Run any shell command. Args: command (str).",
            ),
            StructuredTool.from_function(
                func=lx.get_os_info,
                name="get_os_info",
                description="Get Linux distro info from /etc/os-release.",
            ),
            StructuredTool.from_function(
                func=lx.change_permissions,
                name="change_permissions",
                description="chmod a path. Args: path (str), permissions (str).",
            ),
            StructuredTool.from_function(
                func=lx.change_owner,
                name="change_owner",
                description="chown a path. Args: path (str), owner (str), group (str).",
            ),
        ]

        # ── Tool batches ──────────────────────────────────────────────────────
        self._batches: Dict[str, List[StructuredTool]] = {

            "package": [
                StructuredTool.from_function(func=pkg.update_lists,  name="update_package_lists", description="Run apt-get update."),
                StructuredTool.from_function(func=pkg.install,        name="install_package",      description="Install apt package. Args: package_name (str)."),
                StructuredTool.from_function(func=pkg.remove,         name="remove_package",       description="Remove apt package. Args: package_name (str)."),
                StructuredTool.from_function(func=pkg.is_installed,   name="is_package_installed", description="Check if apt package installed. Args: package_name (str)."),
            ],

            "firewall": [
                StructuredTool.from_function(func=fw.enable,     name="enable_firewall",  description="Enable UFW firewall."),
                StructuredTool.from_function(func=fw.disable,    name="disable_firewall", description="Disable UFW firewall."),
                StructuredTool.from_function(func=fw.allow_port, name="allow_port",       description="Allow a port. Args: port (str), protocol (str)."),
                StructuredTool.from_function(func=fw.deny_port,  name="deny_port",        description="Deny a port. Args: port (str), protocol (str)."),
                StructuredTool.from_function(func=fw.status,     name="firewall_status",  description="Get UFW status."),
            ],

            "user": [
                StructuredTool.from_function(func=usr.create_user,  name="create_user",       description="Create Linux user. Args: username (str)."),
                StructuredTool.from_function(func=usr.delete_user,  name="delete_user",       description="Delete Linux user. Args: username (str)."),
                StructuredTool.from_function(func=usr.add_to_group, name="add_user_to_group", description="Add user to group. Args: username (str), group (str)."),
            ],

            "security": [
                StructuredTool.from_function(func=sec.install_fail2ban,       name="install_fail2ban",       description="Install and enable fail2ban."),
                StructuredTool.from_function(func=sec.disable_root_ssh_login, name="disable_root_ssh_login", description="Disable root SSH login in sshd_config."),
            ],

            "nginx": [
                StructuredTool.from_function(func=nx.install,                  name="install_nginx",       description="Install Nginx via apt."),
                StructuredTool.from_function(func=nx.start,                    name="start_nginx",         description="Start and enable Nginx."),
                StructuredTool.from_function(func=nx.stop,                     name="stop_nginx",          description="Stop Nginx."),
                StructuredTool.from_function(func=nx.restart,                  name="restart_nginx",       description="Restart Nginx."),
                StructuredTool.from_function(func=nx.reload_nginx,             name="reload_nginx",        description="Reload Nginx config."),
                StructuredTool.from_function(func=nx.generate_and_save_config, name="nginx_save_config",   description="Generate and save Nginx site config. Args: framework (str), domain (str), app_name (str), app_path (str), port (int)."),
                StructuredTool.from_function(func=nx.enable_site,              name="nginx_enable_site",   description="Enable Nginx site. Args: app_name (str)."),
                StructuredTool.from_function(func=nx.disable_site,             name="nginx_disable_site",  description="Disable Nginx site. Args: app_name (str)."),
                StructuredTool.from_function(func=nx.test_config,              name="nginx_test_config",   description="Test Nginx config syntax."),
                StructuredTool.from_function(func=nx.delete_site,              name="nginx_delete_site",   description="Delete Nginx site config. Args: app_name (str)."),
                StructuredTool.from_function(func=nx.ensure_ws_map,            name="nginx_ensure_ws_map", description="Ensure WebSocket upgrade map in nginx.conf."),
            ],

            "ssh": [
                StructuredTool.from_function(func=ssh.generate_keypair,   name="generate_ssh_keypair", description="Generate Ed25519 SSH keypair. Args: email (str)."),
                StructuredTool.from_function(func=ssh.get_public_key,     name="get_ssh_public_key",   description="Get server public SSH key."),
                StructuredTool.from_function(func=ssh.add_authorized_key, name="add_authorized_key",   description="Add public key to authorized_keys. Args: public_key (str)."),
            ],

            "systemd": [
                StructuredTool.from_function(func=svc.create_service_file, name="create_systemd_service", description="Create systemd unit file. Args: service_name (str), exec_start (str), working_directory (str), description (str), user (str), restart_policy (str)."),
                StructuredTool.from_function(func=svc.start_service,       name="start_service",          description="Start systemd service. Args: service_name (str)."),
                StructuredTool.from_function(func=svc.stop_service,        name="stop_service",           description="Stop systemd service. Args: service_name (str)."),
                StructuredTool.from_function(func=svc.restart_service,     name="restart_service",        description="Restart systemd service. Args: service_name (str)."),
                StructuredTool.from_function(func=svc.enable_service,      name="enable_service",         description="Enable systemd service on boot. Args: service_name (str)."),
                StructuredTool.from_function(func=svc.check_status,        name="service_status",         description="Get systemd service status. Args: service_name (str)."),
            ],

            "monitor": [
                StructuredTool.from_function(func=mon.get_cpu_usage,  name="get_cpu_usage",  description="Get CPU usage snapshot."),
                StructuredTool.from_function(func=mon.get_ram_usage,  name="get_ram_usage",  description="Get RAM usage."),
                StructuredTool.from_function(func=mon.get_disk_usage, name="get_disk_usage", description="Get disk usage."),
            ],

            "log": [
                StructuredTool.from_function(func=log.read_syslog,     name="read_syslog",     description="Read syslog tail. Args: lines (int)."),
                StructuredTool.from_function(func=log.read_journalctl, name="read_journalctl", description="Read service journal. Args: service (str), lines (int)."),
                StructuredTool.from_function(func=log.read_file_tail,  name="read_log_file",   description="Read any log file tail. Args: filepath (str), lines (int)."),
            ],

            "docker": [
                StructuredTool.from_function(func=dkr.is_installed,  name="is_docker_installed", description="Check if Docker is installed."),
                StructuredTool.from_function(func=dkr.install,       name="install_docker",      description="Install Docker via get.docker.com."),
                StructuredTool.from_function(func=dkr.start_service, name="start_docker",        description="Start and enable Docker service."),
                StructuredTool.from_function(func=dkr.check_status,  name="docker_status",       description="Get Docker service status."),
            ],

            "pm2": [
                StructuredTool.from_function(func=pm2.install,       name="pm2_install",  description="Install PM2 globally via npm."),
                StructuredTool.from_function(func=pm2.start,         name="pm2_start",    description="Start app with PM2. Args: app_name (str), script (str), working_directory (str)."),
                StructuredTool.from_function(func=pm2.stop,          name="pm2_stop",     description="Stop PM2 process. Args: app_name (str)."),
                StructuredTool.from_function(func=pm2.restart,       name="pm2_restart",  description="Restart PM2 process. Args: app_name (str)."),
                StructuredTool.from_function(func=pm2.delete,        name="pm2_delete",   description="Delete PM2 process. Args: app_name (str)."),
                StructuredTool.from_function(func=pm2.status,        name="pm2_status",   description="List all PM2 processes."),
                StructuredTool.from_function(func=pm2.save,          name="pm2_save",     description="Save PM2 process list for reboots."),
                StructuredTool.from_function(func=pm2.setup_startup, name="pm2_startup",  description="Configure PM2 auto-start on boot."),
                StructuredTool.from_function(func=pm2.logs,          name="pm2_logs",     description="Get PM2 process logs. Args: app_name (str), lines (int)."),
            ],
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_full_setup(self, query: str) -> bool:
        q = query.lower()
        return any(kw in q for kw in FULL_SETUP_KEYWORDS)

    def _select_batches(self, query: str) -> List[str]:
        """Return list of batch names matched by query keywords."""
        q = query.lower()
        matched = []
        for keywords, batch_name in _ROUTES:
            if any(kw in q for kw in keywords):
                if batch_name not in matched:
                    matched.append(batch_name)
        # Always include package — most tasks need apt
        if "package" not in matched:
            matched.insert(0, "package")
        return matched

    def _run_batch(self, batch_name: str, query: str) -> str:
        """Run a single batch agent for the given query."""
        tools = self._common + self._batches[batch_name]
        prompt = BATCH_PROMPTS.get(batch_name, "You are a Linux sysadmin. Complete the task using your tools.")
        agent = create_react_agent(self.llm, tools, prompt=prompt)
        result = agent.invoke({"messages": [("user", query)]})
        return result["messages"][-1].content

    # ── Public API ────────────────────────────────────────────────────────────

    def execute_task(self, query: str) -> str:
        """
        Targeted query  → runs only the matching batch(es).
        Full setup query → runs all batches in pipeline order, one by one.
        """
        if self._is_full_setup(query):
            return self._run_full_setup(query)
        else:
            return self._run_targeted(query)

    def _run_targeted(self, query: str) -> str:
        """Route to matched batches and run each one."""
        batch_names = self._select_batches(query)
        results = []
        for batch_name in batch_names:
            logger.info(f"[SetupAgent] Running batch: {batch_name}")
            result = self._run_batch(batch_name, query)
            results.append(f"[{batch_name.upper()}]\n{result}")
        return "\n\n".join(results)

    def _run_full_setup(self, query: str) -> str:
        results = []
        for batch_name in FULL_SETUP_PIPELINE:
            logger.info(f"[SetupAgent] Full setup — running batch: {batch_name}")
            sub_query = f"Focus only on {batch_name} tasks. Do the standard {batch_name} setup now."
            try:
                result = self._run_batch(batch_name, sub_query)
                results.append(f"[{batch_name.upper()}] ✓\n{result}")
                logger.info(f"[SetupAgent] Batch {batch_name} done.")
            except Exception as e:
                error_msg = f"[{batch_name.upper()}] ✗ FAILED: {e}"
                logger.error(error_msg)
                results.append(error_msg)
        return "\n\n" + "\n\n".join(results)