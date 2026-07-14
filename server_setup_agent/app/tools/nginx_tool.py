import re
import base64
from pathlib import Path
from typing import Optional, Union
from loguru import logger
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.executors.base_executor import BaseExecutor


class NginxTool:
    """
    Tool for managing Nginx configuration and sites.

    IMPORTANT: All remote Linux paths are built as plain strings — never via
    pathlib.Path — because Path() on Windows uses backslashes, which would
    produce broken paths when commands are sent to a Linux server over SSH.
    """

    STATIC_FRAMEWORKS = {"react", "vite", "angular", "static"}
    PROXY_FRAMEWORKS  = {"fastapi", "nextjs", "nodejs", "nestjs", "flask", "django", "ai"}

    # SSL cert paths used across all HTTPS configs
    SSL_CERT = "/etc/nginx/ssl/sslcert.crt"
    SSL_KEY  = "/etc/nginx/ssl/sslcert.key"

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

        # Templates dir lives on the Windows host — Path() is fine here
        base_dir = Path(__file__).resolve().parent.parent
        self.templates_dir = base_dir / "templates" / "nginx"

        if not self.templates_dir.exists():
            logger.warning(f"Template directory {self.templates_dir} does not exist. Creating it.")
            self.templates_dir.mkdir(parents=True, exist_ok=True)

        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(),
            auto_reload=True,  # Always reload templates from disk, never use cache
        )

    @staticmethod
    def _is_ip(domain: str) -> bool:
        """
        Returns True if domain is a bare IPv4 address OR the catch-all sentinel '_'.
        Both cases mean "no real domain" → use HTTP-only config, no SSL.
        """
        if not domain or domain.strip() in ("_", "", "none", "null"):
            return True
        return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", domain.strip()))

    def ensure_ssl_cert(self, domain: str) -> str:
        """
        Ensures a TLS certificate exists at /etc/nginx/ssl/.
        - If sslcert.crt already exists → reuse it, no-op.
        - Otherwise → generate a self-signed cert for the domain.
          Self-signed is fine for internal/staging use; swap for a real cert
          (e.g. Let's Encrypt / your CA) any time by replacing the files.
        Returns a status string describing what happened.
        """
        # Create the ssl dir if it doesn't exist
        self.executor.execute("sudo mkdir -p /etc/nginx/ssl")

        # Check if cert already exists
        code, _, _ = self.executor.execute(f"test -f {self.SSL_CERT}")
        if code == 0:
            logger.info(f"SSL cert already exists at {self.SSL_CERT} — reusing.")
            return f"SSL cert already present at {self.SSL_CERT}."

        # Generate self-signed cert (valid 2 years, 2048-bit RSA)
        logger.info(f"No SSL cert found — generating self-signed cert for {domain}")
        cmd = (
            f"sudo openssl req -x509 -nodes -newkey rsa:2048 "
            f"-keyout {self.SSL_KEY} "
            f"-out {self.SSL_CERT} "
            f"-days 730 "
            f"-subj \"/CN={domain}/O=Server/C=US\""
        )
        code, out, err = self.executor.execute(cmd)
        if code != 0:
            raise RuntimeError(f"Failed to generate SSL certificate:\n{err}")

        # Lock down key permissions
        self.executor.execute(f"sudo chmod 600 {self.SSL_KEY}")
        self.executor.execute(f"sudo chmod 644 {self.SSL_CERT}")

        logger.info(f"Self-signed SSL cert generated at {self.SSL_CERT}")
        return (
            f"Self-signed SSL certificate generated at {self.SSL_CERT}.\n"
            f"Replace with a CA-signed cert any time by overwriting those two files."
        )

    def generate_config(
        self,
        framework: str,
        domain: str,
        app_name: str,
        app_path: Optional[str] = None,
        port: Optional[Union[int, str]] = None
    ) -> str:
        """
        Generates an Nginx config string from a Jinja2 template.

        - IP address  → HTTP-only template  (reverse_proxy_http / static_site_http)
        - Domain name → HTTPS template with SSL cert paths  (reverse_proxy / static_site)

        app_path must be a Linux path string (e.g. '/opt/myapp/dist').
        port accepts int or string — will be coerced to int automatically.
        """
        framework_lower = framework.lower()
        is_ip = self._is_ip(domain)

        # Coerce port to int if the LLM passes it as a string
        if port is not None:
            try:
                port = int(port)
            except (ValueError, TypeError):
                port = None

        if framework_lower in self.STATIC_FRAMEWORKS:
            if not app_path:
                raise ValueError(f"app_path is required for static framework '{framework}'")
            # Auto-append /dist for React/Vite/Angular builds if not already present
            if not app_path.rstrip("/").endswith("/dist"):
                app_path = app_path.rstrip("/") + "/dist"

            tpl_name = "static_site_http.conf.j2" if is_ip else "static_site.conf.j2"
            template = self.jinja_env.get_template(tpl_name)
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                app_path=app_path,
            )
            mode = "http-only (IP)" if is_ip else "https (domain)"
            logger.info(f"Generated {tpl_name} for {app_name} ({domain}) mode={mode} root={app_path}")
            return config_str

        elif framework_lower in self.PROXY_FRAMEWORKS:
            if not port:
                raise ValueError(f"port is required for proxy framework '{framework}'")

            tpl_name = "reverse_proxy_http.conf.j2" if is_ip else "reverse_proxy.conf.j2"
            template = self.jinja_env.get_template(tpl_name)
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                port=port,
            )
            mode = "http-only (IP)" if is_ip else "https (domain)"
            logger.info(f"Generated {tpl_name} for {app_name} ({domain} -> {port}) mode={mode}")
            return config_str

        else:
            raise ValueError(
                f"Unsupported framework: {framework}. "
                f"Expected one of {self.STATIC_FRAMEWORKS.union(self.PROXY_FRAMEWORKS)}"
            )

    def generate_and_save_config(
        self,
        framework: str,
        domain: str,
        app_name: str,
        app_path: Optional[str] = None,
        port: Optional[Union[int, str]] = None
    ) -> str:
        """
        Ensures SSL cert (when domain-based), generates config, and saves to disk.
        Returns a status string.
        """
        # For domain deployments, make sure the cert exists before writing the config
        if not self._is_ip(domain):
            cert_status = self.ensure_ssl_cert(domain)
            logger.info(f"[SSL] {cert_status}")

        config_content = self.generate_config(
            framework=framework,
            domain=domain,
            app_name=app_name,
            app_path=app_path,
            port=port,
        )
        self.save_config(app_name, config_content)
        return f"Nginx config generated and saved to /etc/nginx/sites-available/{app_name}.conf"

    def save_config(self, app_name: str, config_content: str) -> str:
        """Saves the config to /etc/nginx/sites-available/<app_name>.conf via sudo tee."""
        # Plain string — no pathlib, no Windows backslashes
        target_path = f"/etc/nginx/sites-available/{app_name}.conf"

        encoded_content = base64.b64encode(config_content.encode("utf-8")).decode("utf-8")
        cmd = f"echo '{encoded_content}' | base64 --decode | sudo tee {target_path} > /dev/null"

        logger.debug(f"Saving Nginx config to {target_path}")
        exit_code, out, err = self.executor.execute(cmd)

        if exit_code != 0:
            logger.error(f"Failed to save Nginx config for {app_name}: {err}")
            raise RuntimeError(f"Failed to save config:\n{err}")

        logger.info(f"Successfully saved Nginx config to {target_path}")
        return "Config saved successfully."

    def enable_site(self, app_name: str) -> str:
        """Enables the site by symlinking into sites-enabled."""
        available_path = f"/etc/nginx/sites-available/{app_name}.conf"
        enabled_path   = f"/etc/nginx/sites-enabled/{app_name}.conf"

        cmd = f"sudo ln -s {available_path} {enabled_path}"
        exit_code, out, err = self.executor.execute(cmd)

        if exit_code != 0:
            check_code, _, _ = self.executor.execute(f"test -L {enabled_path}")
            if check_code == 0:
                logger.info(f"Site {app_name} is already enabled.")
                return f"Site {app_name} already enabled."
            logger.error(f"Failed to enable site {app_name}: {err}")
            raise RuntimeError(f"Failed to enable site:\n{err}")

        logger.info(f"Successfully enabled site {app_name}")
        return f"Site {app_name} enabled."

    def disable_site(self, app_name: str) -> str:
        """Disables the site by removing its symlink from sites-enabled."""
        enabled_path = f"/etc/nginx/sites-enabled/{app_name}.conf"

        exit_code, out, err = self.executor.execute(f"sudo rm -f {enabled_path}")
        if exit_code != 0:
            logger.error(f"Failed to disable site {app_name}: {err}")
            raise RuntimeError(f"Failed to disable site:\n{err}")

        logger.info(f"Successfully disabled site {app_name}")
        return f"Site {app_name} disabled."

    def test_config(self) -> str:
        """Tests the Nginx configuration for syntax errors."""
        exit_code, out, err = self.executor.execute("sudo nginx -t")
        if exit_code != 0:
            logger.error(f"Nginx config test failed: {err}")
            raise RuntimeError(f"Nginx config test failed:\n{err}")
        logger.info("Nginx config test passed.")
        return err if err else out

    def reload_nginx(self) -> str:
        """Reloads Nginx, or starts it if not currently active."""
        exit_code, out, err = self.executor.execute("sudo systemctl reload nginx")
        if exit_code == 0:
            logger.info("Successfully reloaded Nginx.")
            return "DEPLOYMENT COMPLETE. Nginx reloaded successfully."

        if "not active" in err or "not running" in err.lower():
            start_code, start_out, start_err = self.executor.execute(
                "sudo systemctl start nginx"
            )
            if start_code == 0:
                logger.info("Nginx was not running — started successfully.")
                return "DEPLOYMENT COMPLETE. Nginx started successfully."
            raise RuntimeError(f"Failed to start Nginx:\n{start_err}")

        logger.error(f"Failed to reload Nginx: {err}")
        raise RuntimeError(f"Failed to reload Nginx:\n{err}")

    def delete_site(self, app_name: str) -> str:
        """Removes the site config from both available and enabled."""
        self.disable_site(app_name)
        available_path = f"/etc/nginx/sites-available/{app_name}.conf"
        exit_code, out, err = self.executor.execute(f"sudo rm -f {available_path}")
        if exit_code != 0:
            logger.error(f"Failed to delete available config for {app_name}: {err}")
            raise RuntimeError(f"Failed to delete site config:\n{err}")
        logger.info(f"Successfully deleted site {app_name} configs.")
        return f"Site {app_name} deleted."

    def ensure_ws_map(self) -> str:
        """
        Ensures the WebSocket upgrade map block exists in /etc/nginx/nginx.conf.
        This is required for the reverse_proxy template's $connection_upgrade variable.
        Idempotent — safe to call multiple times.
        """
        check_cmd = "grep -q 'connection_upgrade' /etc/nginx/nginx.conf"
        exit_code, _, _ = self.executor.execute(check_cmd)
        if exit_code == 0:
            return "WebSocket map already present in nginx.conf."

        # Append the map block just before the closing } of the http block
        map_block = (
            "map $http_upgrade $connection_upgrade { "
            "default upgrade; '' close; }"
        )
        encoded = base64.b64encode(map_block.encode()).decode()
        cmd = (
            f"sudo sed -i '/^http {{/a\\    "
            f"map $http_upgrade $connection_upgrade {{ default upgrade; '\"'\"''\"'\"' close; }}' "
            f"/etc/nginx/nginx.conf"
        )
        # Simpler: inject via a snippet file in conf.d
        snippet = "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}\n"
        encoded_snippet = base64.b64encode(snippet.encode()).decode()
        write_cmd = (
            f"echo '{encoded_snippet}' | base64 --decode | "
            f"sudo tee /etc/nginx/conf.d/ws_upgrade.conf > /dev/null"
        )
        exit_code, out, err = self.executor.execute(write_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to write WebSocket map snippet: {err}")
        logger.info("WebSocket upgrade map written to /etc/nginx/conf.d/ws_upgrade.conf")
        return "WebSocket map added to /etc/nginx/conf.d/ws_upgrade.conf"

    def install(self) -> str:
        """Installs Nginx."""
        exit_code, out, err = self.executor.execute(
            "sudo apt-get update -y && sudo apt-get install -y nginx"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to install Nginx:\n{err}")
        return "Nginx installed successfully.\n" + out

    def start(self) -> str:
        """Starts and enables Nginx."""
        exit_code, out, err = self.executor.execute(
            "sudo systemctl start nginx && sudo systemctl enable nginx"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to start Nginx:\n{err}")
        return "Nginx started and enabled."

    def stop(self) -> str:
        """Stops Nginx."""
        exit_code, out, err = self.executor.execute("sudo systemctl stop nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to stop Nginx:\n{err}")
        return "Nginx stopped."

    def restart(self) -> str:
        """Restarts Nginx."""
        exit_code, out, err = self.executor.execute("sudo systemctl restart nginx")
        if exit_code != 0:
            raise RuntimeError(f"Failed to restart Nginx:\n{err}")
        return "Nginx restarted."
