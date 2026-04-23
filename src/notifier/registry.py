from __future__ import annotations

import logging

from src.config.models import AppConfig
from src.notifier.base import CompositeNotifier
from src.notifier.email_smtp import SMTPEmailNotifier
from src.notifier.stdout import StdoutNotifier
from src.notifier.webhook import WebhookNotifier


def build_notifier(config: AppConfig) -> CompositeNotifier:
    logger = logging.getLogger("dormalert.notifier")
    notifiers = []

    if config.notification.enable_console:
        notifiers.append(StdoutNotifier())

    if config.notification.webhook_url:
        notifiers.append(
            WebhookNotifier(
                webhook_url=config.notification.webhook_url,
                timeout_seconds=config.notification.webhook_timeout_seconds,
            )
        )

    if config.notification.email_enabled:
        if not (
            config.notification.smtp_host
            and config.notification.email_from
            and config.notification.email_to
        ):
            raise ValueError("SMTP email notifier is enabled but required SMTP settings are missing.")
        notifiers.append(
            SMTPEmailNotifier(
                smtp_host=config.notification.smtp_host,
                smtp_port=config.notification.smtp_port,
                smtp_username=config.notification.smtp_username,
                smtp_password=config.notification.smtp_password,
                smtp_starttls=config.notification.smtp_starttls,
                email_from=config.notification.email_from,
                email_to=config.notification.email_to,
            )
        )

    return CompositeNotifier(notifiers=notifiers, logger=logger)
