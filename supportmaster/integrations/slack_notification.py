"""Slack webhook notification adapter implementing CanSendNotification."""

from __future__ import annotations

from typing import Any

from ..models.control import ExternalOperationReceipt
from ..models.pipeline import NotificationRequest
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class SlackNotificationAdapter:
    """Translation-only adapter connecting SupportMaster to Slack Incoming Webhooks."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        webhook_path: str = "/services/webhook",
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.transport = transport
        self.webhook_path = webhook_path
        self.gateway = gateway or IntegrationGateway()

    def send_notification(
        self, request: NotificationRequest | str, channel: str | None = None
    ) -> ExternalOperationReceipt:
        if isinstance(request, str):
            text = request
            target_channel = channel or "general"
        else:
            text = f"[{request.severity}] {request.subject}: {request.message}" if request.subject else f"[{request.severity}] {request.message}"
            target_channel = request.channel or channel or "general"

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "POST",
                self.webhook_path,
                {"text": text, "channel": target_channel},
            )
            return ExternalOperationReceipt(
                operation_type="SLACK_NOTIFICATION",
                requested_action="send_notification",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=target_channel,
                details={"channel": target_channel, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Slack post failed")),
            )

        return self.gateway.execute(
            permission="SEND_NOTIFICATIONS",
            target=target_channel,
            operation_type="SLACK_NOTIFICATION",
            requested_action="send_notification",
            operation=operation,
            payload={"channel": target_channel, "text": text},
        )
