"""
Teams Alert Service — sends adaptive card alerts to MS Teams via Incoming Webhook.

Alert types:
  - 🔴 CRITICAL  : service down, app crashed, disk full
  - 🟠 WARNING   : high CPU/RAM, high restart count, HTTP errors
  - 🟢 INFO      : deployment complete, auto-restart succeeded
  - 🔵 SECURITY  : suspicious activity, open ports, auth failures

Usage:
    from app.services.teams_alert_service import TeamsAlerter
    alert = TeamsAlerter()
    alert.critical("ats-backend is DOWN", server="192.168.56.101", details="PM2 status: errored")
    alert.warning("High CPU usage", server="192.168.56.101", details="CPU: 94%")
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from app.core.config import settings


class TeamsAlerter:

    COLOURS = {
        "critical": "FF0000",   # red
        "warning":  "FF8C00",   # orange
        "info":     "00B050",   # green
        "security": "0078D4",   # blue
    }

    ICONS = {
        "critical": "🔴",
        "warning":  "🟠",
        "info":     "🟢",
        "security": "🔵",
    }

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.TEAMS_WEBHOOK_URL
        if not self.webhook_url:
            logger.warning(
                "[TEAMS] TEAMS_WEBHOOK_URL not configured. "
                "Set it in .env to enable Teams alerts."
            )

    def _send(self, level: str, title: str, server: str, details: str = "") -> bool:
        """
        Sends an Adaptive Card message to Teams.
        Returns True on success, False on failure.
        """
        if not self.webhook_url:
            logger.info(f"[TEAMS ALERT - {level.upper()}] {title} | {server} | {details}")
            return False

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        icon = self.ICONS.get(level, "ℹ️")
        colour = self.COLOURS.get(level, "0078D4")

        # MS Teams Adaptive Card payload
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "Container",
                                "style": "emphasis",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": f"{icon} {level.upper()} — Server Maintenance AI",
                                        "weight": "Bolder",
                                        "size": "Medium",
                                        "color": "Accent",
                                    }
                                ]
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Alert",  "value": title},
                                    {"title": "Server", "value": server},
                                    {"title": "Time",   "value": now},
                                ]
                            },
                            *(
                                [
                                    {
                                        "type": "TextBlock",
                                        "text": f"**Details:** {details}",
                                        "wrap": True,
                                        "spacing": "Small",
                                    }
                                ]
                                if details else []
                            ),
                        ],
                        "msteams": {"width": "Full"},
                    },
                }
            ],
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                if resp.status in (200, 202):
                    logger.info(f"[TEAMS] Alert sent: {level.upper()} — {title}")
                    return True
                logger.warning(f"[TEAMS] Unexpected response: {resp.status} {body}")
                return False
        except urllib.error.URLError as e:
            logger.error(f"[TEAMS] Failed to send alert: {e}")
            return False

    def critical(self, title: str, server: str = "unknown", details: str = "") -> bool:
        """Send a critical alert (service down, crash, disk full)."""
        return self._send("critical", title, server, details)

    def warning(self, title: str, server: str = "unknown", details: str = "") -> bool:
        """Send a warning alert (high CPU/RAM, many restarts, HTTP errors)."""
        return self._send("warning", title, server, details)

    def info(self, title: str, server: str = "unknown", details: str = "") -> bool:
        """Send an info alert (deployment done, auto-restart succeeded)."""
        return self._send("info", title, server, details)

    def security(self, title: str, server: str = "unknown", details: str = "") -> bool:
        """Send a security alert (open ports, auth failures, suspicious activity)."""
        return self._send("security", title, server, details)
