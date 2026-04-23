from __future__ import annotations

import logging

from src.config.models import AppConfig
from src.notifier.base import CompositeNotifier
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

    return CompositeNotifier(notifiers=notifiers, logger=logger)

