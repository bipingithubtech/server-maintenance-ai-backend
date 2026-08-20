from loguru import logger
from app.executors.base_executor import BaseExecutor

class FirewallTool:
    """Tool for managing UFW (Uncomplicated Firewall)."""

    def __init__(self, executor: BaseExecutor):
        self.executor = executor

    def enable(self) -> str:
        """Enables the firewall."""
        # First check if UFW is installed
        check_code, check_out, check_err = self.executor.execute("which ufw")
        if check_code != 0:
            logger.warning("UFW not installed on server")
            return "⚠️ UFW is not installed on this server.\n\nTo install UFW, run manually:\nsudo apt-get update && sudo apt-get install -y ufw"
        
        # Check if already enabled
        status_code, status_out, status_err = self.executor.execute("sudo ufw status")
        if status_code == 0 and "active" in status_out.lower():
            logger.info("UFW is already enabled")
            return "✅ Firewall is already enabled (status: active)"
        
        # Try to enable with --force-enable
        exit_code, out, err = self.executor.execute("sudo ufw --force-enable")
        if exit_code != 0:
            logger.error(f"Failed to enable UFW with --force-enable. Trying standard enable...")
            # Fallback: try without --force-enable
            exit_code, out, err = self.executor.execute("sudo ufw enable")
            if exit_code != 0:
                logger.error(f"Failed to enable UFW: stdout={out}, stderr={err}, exit_code={exit_code}")
                return f"⚠️ Failed to enable firewall.\n\nDebug info:\n- Stdout: {out}\n- Stderr: {err}\n\nTry running manually: sudo ufw enable"
        
        logger.info(f"UFW enabled successfully. Output: {out}")
        return f"✅ Firewall enabled successfully\n{out}"

    def disable(self) -> str:
        """Disables the firewall."""
        exit_code, out, err = self.executor.execute("sudo ufw disable")
        if exit_code != 0:
            # Return error message instead of raising (to avoid 500 error)
            logger.error(f"Failed to disable UFW: {err}")
            return f"⚠️ Failed to disable firewall:\n{err}\n\nTry running manually: sudo ufw disable"
        return out

    def allow_port(self, port: str, protocol: str = "tcp") -> str:
        """Allows traffic on a specific port and protocol."""
        exit_code, out, err = self.executor.execute(f"sudo ufw allow {port}/{protocol}")
        if exit_code != 0:
            # Return error message instead of raising (to avoid 500 error)
            logger.error(f"Failed to allow port {port}/{protocol}: {err}")
            return f"⚠️ Failed to allow port {port}/{protocol}:\n{err}\n\nTry manually: sudo ufw allow {port}/{protocol}"
        return out

    def deny_port(self, port: str, protocol: str = "tcp") -> str:
        """Denies traffic on a specific port and protocol."""
        exit_code, out, err = self.executor.execute(f"sudo ufw deny {port}/{protocol}")
        if exit_code != 0:
            # Return error message instead of raising (to avoid 500 error)
            logger.error(f"Failed to deny port {port}/{protocol}: {err}")
            return f"⚠️ Failed to deny port {port}/{protocol}:\n{err}\n\nTry manually: sudo ufw deny {port}/{protocol}"
        return out

    def status(self) -> str:
        """Gets the current status of the firewall."""
        exit_code, out, err = self.executor.execute("sudo ufw status verbose")
        if exit_code != 0:
            # Return error message instead of raising (to avoid 500 error)
            logger.error(f"Failed to get UFW status: {err}")
            return f"⚠️ Failed to get firewall status:\n{err}\n\nTry manually: sudo ufw status verbose"
        return out

    def allow_ports_batch(self, ports: list) -> str:
        """Allows multiple ports at once. Returns status after all ports are added."""
        results = []
        for port in ports:
            try:
                result = self.allow_port(port)
                results.append(f"Port {port}: OK")
            except RuntimeError as e:
                results.append(f"Port {port}: FAILED - {str(e)}")
            except Exception as e:
                results.append(f"Port {port}: ERROR - {str(e)}")
        return "\n".join(results)

    def delete_rule(self, port: str, protocol: str = "tcp", action: str = "allow") -> str:
        """Removes a firewall rule for a specific port."""
        # SAFETY: This is a destructive operation
        logger.critical(f"[SECURITY] DELETE FIREWALL RULE ATTEMPT: {port}/{protocol} ({action})")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="⚠️ DELETE FIREWALL RULE REQUESTED",
            server="Server",
            details=f"Firewall rule deletion request: {action} {port}/{protocol}\n\n"
                   f"This will remove the firewall rule permanently.\n"
                   f"REQUIRES EXPLICIT CONFIRMATION from administrator."
        )
        
        # Return error message instead of raising (to avoid 500 error)
        return (
            f"❌ BLOCKED: Firewall rule deletion is a destructive operation.\n\n"
            f"Rule to delete: {action} {port}/{protocol}\n\n"
            f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
            f"To proceed, you must:\n"
            f"1. Verify this is intentional\n"
            f"2. Get explicit approval from security admin\n"
            f"3. Run manually via SSH:\n"
            f"   sudo ufw delete {action} {port}/{protocol}"
        )

    def delete_rules_batch(self, ports: list) -> str:
        """Removes multiple firewall rules at once."""
        # SAFETY: This is a destructive operation
        logger.critical(f"[SECURITY] DELETE MULTIPLE FIREWALL RULES ATTEMPT: {len(ports)} rules")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="⚠️ DELETE MULTIPLE FIREWALL RULES REQUESTED",
            server="Server",
            details=f"Batch firewall rules deletion request: {len(ports)} rules\n\n"
                   f"Ports: {', '.join(ports)}\n\n"
                   f"This is a BULK destructive operation.\n"
                   f"REQUIRES EXPLICIT CONFIRMATION from security admin."
        )
        
        # Build command examples without f-string backslash issue
        port_cmds = "\n   ".join(f"sudo ufw delete allow {port}" for port in ports[:3])
        error_msg = (
            f"❌ BLOCKED: Batch firewall rules deletion is a destructive operation.\n\n"
            f"Rules to delete: {len(ports)} ports\n"
            f"Ports: {', '.join(ports)}\n\n"
            f"⚠️ SECURITY ALERT sent to Microsoft Teams.\n\n"
            f"To proceed, you must:\n"
            f"1. Verify this is intentional\n"
            f"2. Get explicit approval from security admin\n"
            f"3. Run manually via SSH for each port:\n"
            f"   {port_cmds}\n"
            f"   ... (and so on for remaining ports)"
        )
        # Return error message instead of raising (to avoid 500 error)
        return error_msg

    def reset_firewall(self) -> str:
        """Resets firewall to default state (removes all rules)."""
        # SAFETY: This is an EXTREMELY destructive operation - removes ALL rules!
        logger.critical(f"[SECURITY] RESET FIREWALL ATTEMPT - THIS DELETES ALL RULES!")
        
        # Send Teams alert
        from app.services.teams_alert_service import TeamsAlerter
        alerter = TeamsAlerter()
        alerter.critical(
            title="🚨 FIREWALL RESET REQUESTED - CRITICAL SECURITY ALERT",
            server="Server",
            details=f"FIREWALL RESET REQUEST - This will DELETE ALL firewall rules!\n\n"
                   f"This is an EXTREMELY DANGEROUS operation.\n"
                   f"Server will be left with NO firewall protection.\n\n"
                   f"REQUIRES EXPLICIT APPROVAL from SECURITY TEAM LEAD.\n\n"
                   f"If this was NOT authorized, please investigate immediately."
        )
        
        error_msg = (
            "❌ BLOCKED: Firewall reset is an EXTREMELY destructive operation!\n\n"
            "This will DELETE ALL firewall rules and leave the server unprotected.\n\n"
            "🚨 CRITICAL SECURITY ALERT sent to Microsoft Teams.\n\n"
            "To proceed, you MUST:\n"
            "1. Have explicit written approval from Security Team Lead\n"
            "2. Verify this is absolutely necessary\n"
            "3. Have a backup security plan ready\n"
            "4. Run manually via SSH:\n"
            "   sudo ufw --force reset\n\n"
            "⚠️ This is logged and audited for security compliance."
        )
        # Return error message instead of raising (to avoid 500 error)
        return error_msg
