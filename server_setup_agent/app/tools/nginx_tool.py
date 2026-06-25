import base64
from pathlib import Path
from typing import Optional
from loguru import logger
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.executors.base_executor import BaseExecutor


class NginxTool:
    """
    Production-ready tool for managing Nginx configuration and sites.
    Provides dynamic config generation via Jinja2 templates, and
    deployment functions using the Executor pattern.
    """

    # Framework mapping dictionaries
    STATIC_FRAMEWORKS = {"react", "vite", "angular", "static"}
    PROXY_FRAMEWORKS = {"fastapi", "nextjs", "nodejs", "nestjs", "flask", "django", "ai"}

    def __init__(self, executor: BaseExecutor):
        self.executor = executor
        
        # Determine absolute path to templates directory dynamically
        base_dir = Path(__file__).resolve().parent.parent
        self.templates_dir = base_dir / "templates" / "nginx"
        
        if not self.templates_dir.exists():
            logger.warning(f"Template directory {self.templates_dir} does not exist. Creating it.")
            self.templates_dir.mkdir(parents=True, exist_ok=True)
            
        # Initialize Jinja2 environment
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape()
        )

    def generate_config(
        self,
        framework: str,
        domain: str,
        app_name: str,
        app_path: Optional[str] = None,
        port: Optional[int] = None
    ) -> str:
        """
        Generates an Nginx config string using Jinja2 templates based on the framework.

        Args:
            framework: The framework being deployed (e.g., react, fastapi).
            domain: The domain name or IP (e.g., api.example.com).
            app_name: The unique application identifier.
            app_path: The absolute path to the static build directory (required for static).
            port: The internal localhost port the app runs on (required for proxy).

        Returns:
            The rendered Nginx configuration as a string.
            
        Raises:
            ValueError: If the framework is unsupported or required parameters are missing.
        """
        framework_lower = framework.lower()
        
        if framework_lower in self.STATIC_FRAMEWORKS:
            if not app_path:
                raise ValueError(f"app_path is required for static framework '{framework}'")
            template = self.jinja_env.get_template("static_site.conf.j2")
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                app_path=str(Path(app_path).resolve())
            )
            logger.info(f"Generated static_site config for {app_name} ({domain})")
            return config_str
            
        elif framework_lower in self.PROXY_FRAMEWORKS:
            if not port:
                raise ValueError(f"port is required for proxy framework '{framework}'")
            template = self.jinja_env.get_template("reverse_proxy.conf.j2")
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                port=port
            )
            logger.info(f"Generated reverse_proxy config for {app_name} ({domain} -> {port})")
            return config_str
            
        else:
            raise ValueError(f"Unsupported framework: {framework}. Expected one of {self.STATIC_FRAMEWORKS.union(self.PROXY_FRAMEWORKS)}")

    def save_config(self, app_name: str, config_content: str) -> str:
        """
        Saves the generated config to /etc/nginx/sites-available/{app_name}.conf.
        Uses base64 encoding piped to sudo tee to safely write the file.
        """
        target_path = Path(f"/etc/nginx/sites-available/{app_name}.conf")
        
        # Base64 encode the config to prevent shell injection / escaping errors
        encoded_content = base64.b64encode(config_content.encode('utf-8')).decode('utf-8')
        
        cmd = f"echo '{encoded_content}' | base64 --decode | sudo tee {target_path} > /dev/null"
        
        logger.debug(f"Saving Nginx config to {target_path}")
        exit_code, out, err = self.executor.execute(cmd)
        
        if exit_code != 0:
            logger.error(f"Failed to save Nginx config for {app_name}: {err}")
            raise RuntimeError(f"Failed to save config:\n{err}")
            
        logger.info(f"Successfully saved Nginx config to {target_path}")
        return "Config saved successfully."

    def enable_site(self, app_name: str) -> str:
        """
        Enables the site by creating a symbolic link in sites-enabled.
        """
        available_path = Path(f"/etc/nginx/sites-available/{app_name}.conf")
        enabled_path = Path(f"/etc/nginx/sites-enabled/{app_name}.conf")
        
        cmd = f"sudo ln -s {available_path} {enabled_path}"
        exit_code, out, err = self.executor.execute(cmd)
        
        if exit_code != 0:
            # Check if it failed just because the symlink already exists
            check_code, _, _ = self.executor.execute(f"test -L {enabled_path}")
            if check_code == 0:
                logger.info(f"Site {app_name} is already enabled.")
                return f"Site {app_name} already enabled."
                
            logger.error(f"Failed to enable site {app_name}: {err}")
            raise RuntimeError(f"Failed to enable site:\n{err}")
            
        logger.info(f"Successfully enabled site {app_name}")
        return f"Site {app_name} enabled."

    def disable_site(self, app_name: str) -> str:
        """
        Disables the site by removing the symbolic link from sites-enabled.
        """
        enabled_path = Path(f"/etc/nginx/sites-enabled/{app_name}.conf")
        
        cmd = f"sudo rm -f {enabled_path}"
        exit_code, out, err = self.executor.execute(cmd)
        
        if exit_code != 0:
            logger.error(f"Failed to disable site {app_name}: {err}")
            raise RuntimeError(f"Failed to disable site:\n{err}")
            
        logger.info(f"Successfully disabled site {app_name}")
        return f"Site {app_name} disabled."

    def test_config(self) -> str:
        """
        Tests the Nginx configuration.
        """
        exit_code, out, err = self.executor.execute("sudo nginx -t")
        
        if exit_code != 0:
            logger.error(f"Nginx config test failed: {err}")
            raise RuntimeError(f"Nginx config test failed:\n{err}")
            
        logger.info("Nginx config test passed.")
        return err if err else out

    def reload_nginx(self) -> str:
        """
        Reloads the Nginx service.
        """
        exit_code, out, err = self.executor.execute("sudo systemctl reload nginx")
        
        if exit_code != 0:
            logger.error(f"Failed to reload Nginx: {err}")
            raise RuntimeError(f"Failed to reload Nginx:\n{err}")
            
        logger.info("Successfully reloaded Nginx.")
        return "Nginx reloaded successfully."

    def delete_site(self, app_name: str) -> str:
        """
        Completely deletes the site configuration from available and enabled directories.
        """
        self.disable_site(app_name)
        
        available_path = Path(f"/etc/nginx/sites-available/{app_name}.conf")
        cmd = f"sudo rm -f {available_path}"
        
        exit_code, out, err = self.executor.execute(cmd)
        
        if exit_code != 0:
            logger.error(f"Failed to delete available config for {app_name}: {err}")
            raise RuntimeError(f"Failed to delete site config:\n{err}")
            
        logger.info(f"Successfully deleted site {app_name} configs.")
        return f"Site {app_name} deleted."

    def install(self) -> str:
        """Installs Nginx (Legacy method kept for SetupAgent compatibility)"""
        exit_code, out, err = self.executor.execute("sudo apt-get update -y && sudo apt-get install -y nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to install Nginx:\n{err}")
        return "Nginx installed successfully.\n" + out

    def start(self) -> str:
        """Starts and enables Nginx (Legacy method kept for SetupAgent compatibility)"""
        exit_code, out, err = self.executor.execute("sudo systemctl start nginx && sudo systemctl enable nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to start Nginx:\n{err}")
        return "Nginx started and enabled."

    def stop(self) -> str:
        """Stops Nginx (Legacy method kept for SetupAgent compatibility)"""
        exit_code, out, err = self.executor.execute("sudo systemctl stop nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to stop Nginx:\n{err}")
        return "Nginx stopped."

    def restart(self) -> str:
        """Restarts Nginx (Legacy method kept for SetupAgent compatibility)"""
        exit_code, out, err = self.executor.execute("sudo systemctl restart nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart Nginx:\n{err}")
        return "Nginx restarted."
