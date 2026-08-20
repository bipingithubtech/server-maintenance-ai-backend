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

    STATIC_FRAMEWORKS = {"static"}  # Only pure static HTML sites
    PROXY_FRAMEWORKS  = {"react", "vite", "angular", "fastapi", "nextjs", "nodejs", "nestjs", "flask", "django", "ai"}

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
        Ensures an SSL certificate exists for the domain.
        - For IP-based deployments: skip (HTTP only)
        - For domains: Skip self-signed for now - use HTTP-only config
        
        SSL/TLS should be configured separately after initial deployment.
        
        Returns a status string describing what happened.
        """
        if not domain or self._is_ip(domain):
            logger.info(f"Skipping SSL cert for {domain} (IP-based, not domain)")
            return "SSL cert not needed for IP-based deployments."

        # Check if certificate already exists
        cert_path = f"/etc/letsencrypt/live/{domain}"
        exit_code, out, err = self.executor.execute(f"test -d {cert_path} && echo 'exists'")
        
        if exit_code == 0 and "exists" in out:
            logger.info(f"✓ SSL certificate for {domain} already exists at {cert_path}")
            return f"SSL certificate found at {cert_path} - will use for HTTPS configuration"
        
        # Certificate doesn't exist - skip generation during deployment
        # SSH terminal issues with sudo make this unreliable during deployment
        # User can configure SSL separately using certbot on the server
        logger.info(f"Skipping SSL cert generation for {domain} - configure separately after deployment")
        return f"SSL cert skipped for initial deployment. Configure Let's Encrypt separately on the server using: sudo certbot certonly --standalone -d {domain}"

    def _generate_self_signed_fallback(self, domain: str) -> str:
        """
        Fallback to self-signed cert if Let's Encrypt fails.
        Useful for staging/internal environments.
        
        Generates cert in temp location, checks if /etc/letsencrypt is writable.
        If not, stores in /tmp for now (user can manually move to nginx later).
        """
        temp_key = f"/tmp/{domain}.key.tmp"
        temp_cert = f"/tmp/{domain}.crt.tmp"
        
        # Generate certificate in /tmp (no sudo needed)
        cmd = (
            f"openssl req -x509 -nodes -newkey rsa:2048 "
            f"-keyout {temp_key} "
            f"-out {temp_cert} "
            f"-days 365 "
            f"-subj \"/CN={domain}/O=Server/C=US\""
        )
        
        code, out, err = self.executor.execute(cmd)
        if code != 0:
            raise RuntimeError(f"Failed to generate self-signed fallback cert:\n{err}")
        
        logger.info(f"Certificate generated in {temp_cert} (temp location)")
        
        # Try to move to final location - but do it as separate commands, not chained
        cert_dir = f"/etc/letsencrypt/live/{domain}"
        
        # Step 1: Create directory (try without sudo first)
        code, _, _ = self.executor.execute(f"mkdir -p {cert_dir} 2>/dev/null")
        if code != 0:
            # Need sudo - try it
            code, _, err = self.executor.execute(f"sudo mkdir -p {cert_dir}")
            if code != 0:
                logger.warning(f"Cannot create {cert_dir}, will use temp location: {err}")
                logger.warning(f"Certificate available at {temp_cert} - copy to nginx later")
                return f"⚠️ Self-signed certificate generated at {temp_cert}. Cannot write to {cert_dir} without elevated privileges."
        
        # Step 2: Move key file
        code, _, err = self.executor.execute(f"sudo mv {temp_key} {cert_dir}/privkey.pem 2>/dev/null")
        if code != 0:
            logger.warning(f"Could not move key with sudo, trying without: {err}")
            code, _, _ = self.executor.execute(f"cp {temp_key} {cert_dir}/privkey.pem 2>/dev/null")
            if code != 0:
                logger.warning(f"Keeping cert in temp location: {temp_cert}")
                return f"⚠️ Self-signed certificate generated at {temp_cert}. Please copy to {cert_dir} manually."
        
        # Step 3: Move cert file
        code, _, err = self.executor.execute(f"sudo mv {temp_cert} {cert_dir}/fullchain.pem 2>/dev/null")
        if code != 0:
            logger.warning(f"Could not move cert with sudo, trying without: {err}")
            code, _, _ = self.executor.execute(f"cp {temp_cert} {cert_dir}/fullchain.pem 2>/dev/null")
            if code != 0:
                logger.warning(f"Keeping cert in temp location: {temp_cert}")
                return f"⚠️ Self-signed certificate generated at {temp_cert}. Please copy to {cert_dir} manually."
        
        # Step 4: Set permissions
        self.executor.execute(f"sudo chmod 600 {cert_dir}/privkey.pem 2>/dev/null")
        self.executor.execute(f"sudo chmod 644 {cert_dir}/fullchain.pem 2>/dev/null")
        
        logger.warning(f"Self-signed fallback cert generated at {cert_dir} (not Let's Encrypt)")
        return f"⚠️ Self-signed certificate generated as fallback for {domain}. Please configure Let's Encrypt properly."

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
        - Domain name WITHOUT cert → HTTP-only template (will use HTTPS later)
        - Domain name WITH cert → HTTPS template with SSL cert paths

        app_path must be a Linux path string (e.g. '/opt/myapp/dist').
        port accepts int or string — will be coerced to int automatically.
        
        IMPORTANT: When domain is empty or "_", it's replaced with app_name for proper
        SSL cert paths. This ensures SSL certificates use the app name, not the generic "_".
        """
        framework_lower = framework.lower()
        
        # If domain is empty or "_", use app_name instead for SSL cert paths
        # This prevents SSL cert lookup failures
        if not domain or domain.strip() in ("_", "", "none", "null"):
            domain = app_name
        
        is_ip = self._is_ip(domain)
        
        # For domain-based deployments:
        # - Generate HTTP-only config initially (with ACME challenge support)
        # - Certbot nginx plugin will upgrade it to HTTPS later
        # Only use HTTPS template if cert already exists
        has_cert = False
        if not is_ip:
            cert_dir = f"/etc/letsencrypt/live/{domain}"
            code, _, _ = self.executor.execute(f"test -d {cert_dir}")
            has_cert = (code == 0)

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

            # Use HTTP-only if no cert, even for domain names
            use_http_only = is_ip or not has_cert
            tpl_name = "static_site_http.conf.j2" if use_http_only else "static_site.conf.j2"
            template = self.jinja_env.get_template(tpl_name)
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                app_path=app_path,
            )
            mode = "http-only (no cert)" if use_http_only else "https (with cert)"
            logger.info(f"Generated {tpl_name} for {app_name} ({domain}) mode={mode} root={app_path}")
            return config_str

        elif framework_lower in self.PROXY_FRAMEWORKS:
            if not port:
                raise ValueError(f"port is required for proxy framework '{framework}'")

            # Use HTTP-only if no cert, even for domain names
            use_http_only = is_ip or not has_cert
            tpl_name = "reverse_proxy_http.conf.j2" if use_http_only else "reverse_proxy.conf.j2"
            template = self.jinja_env.get_template(tpl_name)
            config_str = template.render(
                domain=domain,
                app_name=app_name,
                port=port,
            )
            mode = "http-only (no cert)" if use_http_only else "https (with cert)"
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
        self.save_config(app_name, config_content, domain)
        
        # Determine config name for status message
        config_name = domain if (domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null")) else app_name
        return f"Nginx config generated and saved to /etc/nginx/sites-available/{config_name}"

    def save_config(self, app_name: str, config_content: str, domain: Optional[str] = None) -> str:
        """
        Saves the config to /etc/nginx/sites-available/ via base64 encoding.
        
        If domain is provided, uses {domain} naming (NO .conf extension in the name).
        Otherwise falls back to {app_name} for backwards compatibility.
        """
        # Use domain-based naming if domain is provided and valid
        if domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null"):
            config_name = domain  # e.g., "deploy.meetri.in"
        else:
            config_name = app_name
        
        # Plain string — no pathlib, no Windows backslashes
        target_path = f"/etc/nginx/sites-available/{config_name}"

        # Write to temp file first (no sudo needed)
        temp_path = f"/tmp/{config_name}.tmp"
        encoded_content = base64.b64encode(config_content.encode("utf-8")).decode("utf-8")
        
        # Write to temp location (no sudo needed)
        write_cmd = f"echo '{encoded_content}' | base64 --decode > {temp_path}"
        code, out, err = self.executor.execute(write_cmd)
        if code != 0:
            logger.error(f"Failed to write temp config: {err}")
            raise RuntimeError(f"Failed to write temp config:\n{err}")
        
        # Now move it with sudo (this is faster than piping through sudo tee)
        move_cmd = f"sudo mv {temp_path} {target_path} && sudo chmod 644 {target_path}"
        code, out, err = self.executor.execute(move_cmd)
        
        if code != 0:
            logger.error(f"Failed to save Nginx config for {app_name}: {err}")
            raise RuntimeError(f"Failed to save config:\n{err}")

        logger.info(f"Successfully saved Nginx config to {target_path}")
        return "Config saved successfully."

    def enable_site(self, app_name: str, domain: Optional[str] = None) -> str:
        """
        Enables the site by symlinking into sites-enabled.
        Uses domain name if provided, otherwise app_name.
        """
        # Use domain-based naming if domain is provided and valid
        if domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null"):
            config_name = domain
        else:
            config_name = app_name
        
        available_path = f"/etc/nginx/sites-available/{config_name}"
        enabled_path   = f"/etc/nginx/sites-enabled/{config_name}"

        # First check if already enabled
        check_code, _, _ = self.executor.execute(f"test -L {enabled_path}")
        if check_code == 0:
            logger.info(f"Site {app_name} is already enabled.")
            return f"Site {app_name} already enabled."
        
        # Create symlink with sudo
        cmd = f"sudo ln -s {available_path} {enabled_path}"
        exit_code, out, err = self.executor.execute(cmd)

        if exit_code != 0:
            logger.error(f"Failed to enable site {app_name}: {err}")
            raise RuntimeError(f"Failed to enable site:\n{err}")

        logger.info(f"Successfully enabled site {config_name}")
        return f"Site {config_name} enabled."

    def disable_site(self, app_name: str, domain: Optional[str] = None) -> str:
        """
        Disables the site by removing its symlink from sites-enabled.
        Uses domain name if provided, otherwise app_name.
        """
        # Use domain-based naming if domain is provided and valid
        if domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null"):
            config_name = domain
        else:
            config_name = app_name
        
        enabled_path = f"/etc/nginx/sites-enabled/{config_name}"

        exit_code, out, err = self.executor.execute(f"sudo rm -f {enabled_path}")
        if exit_code != 0:
            logger.error(f"Failed to disable site {app_name}: {err}")
            raise RuntimeError(f"Failed to disable site:\n{err}")

        logger.info(f"Successfully disabled site {config_name}")
        return f"Site {config_name} disabled."

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

    def update_config_with_ssl(self, app_name: str, domain: str) -> str:
        """
        Updates an existing Nginx config to use HTTPS/SSL after a certificate is obtained.
        This is called from ops_agent after certbot completes.
        """
        # Use domain-based naming if domain is provided and valid
        if domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null"):
            config_name = domain
        else:
            config_name = app_name
        
        # Read existing config
        available_path = f"/etc/nginx/sites-available/{config_name}"
        
        try:
            code, config_content, err = self.executor.execute(f"cat {available_path}")
            if code != 0:
                raise RuntimeError(f"Cannot read config: {err}")
        except Exception as e:
            raise RuntimeError(f"Failed to read existing config: {e}")
        
        # Check if it already uses HTTPS
        if "ssl_certificate" in config_content:
            logger.info(f"Config for {app_name} already has SSL directives")
            return f"Config for {app_name} already has SSL certificates configured."
        
        # Add SSL cert paths to the config
        # This is a simplified approach - just inject SSL directives
        cert_dir = f"/etc/letsencrypt/live/{domain}"
        ssl_cert = f"{cert_dir}/fullchain.pem"
        ssl_key = f"{cert_dir}/privkey.pem"
        
        # Inject SSL configuration
        ssl_block = (
            f"\n    ssl_certificate {ssl_cert};\n"
            f"    ssl_certificate_key {ssl_key};\n"
            f"    ssl_protocols TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers HIGH:!aNULL:!MD5;\n"
        )
        
        # Replace first server block to enable SSL
        updated_config = config_content.replace(
            "server {",
            f"server {{\n    listen 443 ssl http2;\n    listen [::]:443 ssl http2;{ssl_block}",
            1
        )
        
        # Also add HTTP redirect
        http_redirect = f"""
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}

"""
        updated_config = http_redirect + updated_config
        
        # Save updated config
        self.save_config(app_name, updated_config, domain)
        
        # Test and reload
        self.test_config()
        self.reload_nginx()
        
        logger.info(f"Updated {config_name} config with SSL certificates")
        return f"✅ Config updated with SSL certificates for {domain}"
    
    def delete_site(self, app_name: str, domain: Optional[str] = None) -> str:
        """
        Removes the site config from both available and enabled.
        Uses domain name if provided, otherwise app_name.
        """
        # SAFETY: This is a destructive operation
        config_name = domain if (domain and not self._is_ip(domain) and domain.strip() not in ("_", "", "none", "null")) else app_name
        
        logger.critical(f"[SECURITY] DELETE NGINX SITE ATTEMPT: {config_name}")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="⚠️ DELETE NGINX SITE REQUESTED",
            server="Server",
            details=f"Nginx site deletion request: {config_name}\n\n"
                   f"This will remove the site configuration permanently.\n"
                   f"REQUIRES EXPLICIT CONFIRMATION from administrator."
        )
        
        raise RuntimeError(
            f"❌ BLOCKED: Nginx site deletion is a destructive operation.\n\n"
            f"Site to delete: {config_name}\n\n"
            f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
            f"To proceed, you must:\n"
            f"1. Verify this is intentional\n"
            f"2. Get explicit approval from team lead\n"
            f"3. Run manually via SSH:\n"
            f"   sudo rm -f /etc/nginx/sites-available/{config_name}\n"
            f"   sudo rm -f /etc/nginx/sites-enabled/{config_name}\n"
            f"   sudo nginx -t && sudo systemctl reload nginx"
        )

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

        # Write WebSocket map via temp file (avoiding sudo tee issue with piped input)
        snippet = "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}\n"
        encoded_snippet = base64.b64encode(snippet.encode()).decode()
        
        # Step 1: Write to temp file (no sudo)
        write_cmd = f"echo '{encoded_snippet}' | base64 --decode > /tmp/ws_upgrade.conf"
        exit_code, out, err = self.executor.execute(write_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to write temp WebSocket map: {err}")
        
        # Step 2: Move to final location with sudo (single command, no pipe)
        move_cmd = f"sudo mv /tmp/ws_upgrade.conf /etc/nginx/conf.d/ws_upgrade.conf && sudo chmod 644 /etc/nginx/conf.d/ws_upgrade.conf"
        exit_code, out, err = self.executor.execute(move_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to move WebSocket map snippet: {err}")
        
        logger.info("WebSocket upgrade map written to /etc/nginx/conf.d/ws_upgrade.conf")
        return "WebSocket map added to /etc/nginx/conf.d/ws_upgrade.conf"

    def install(self) -> str:
        """Installs Nginx if not already installed."""
        # Check if nginx is already installed
        code, _, _ = self.executor.execute("which nginx")
        if code == 0:
            logger.info("✓ Nginx is already installed (skipping installation).")
            return "Nginx is already installed (skipping)."
        
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
