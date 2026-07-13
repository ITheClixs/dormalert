from __future__ import annotations

import logging

from src.config.models import AppConfig
from src.notifier.base import CompositeNotifier
from src.notifier.callmebot import CallMeBotWhatsAppNotifier
from src.notifier.email_smtp import SMTPEmailNotifier
from src.notifier.stdout import StdoutNotifier
from src.notifier.telegram import TelegramNotifier
from src.notifier.webhook import WebhookNotifier
from src.notifier.whatsapp_cloud import MetaWhatsAppCloudNotifier


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

    if config.notification.whatsapp_enabled:
        if not (config.notification.whatsapp_phone and config.notification.whatsapp_apikey):
            raise ValueError(
                "WhatsApp notifier is enabled but DORMALERT_WHATSAPP_PHONE and "
                "DORMALERT_WHATSAPP_APIKEY are required."
            )
        notifiers.append(
            CallMeBotWhatsAppNotifier(
                phone=config.notification.whatsapp_phone,
                apikey=config.notification.whatsapp_apikey,
                timeout_seconds=config.notification.whatsapp_timeout_seconds,
            )
        )

    if config.notification.wa_cloud_enabled:
        if not (
            config.notification.wa_cloud_token
            and config.notification.wa_cloud_phone_number_id
            and config.notification.wa_cloud_to
        ):
            raise ValueError(
                "WhatsApp Cloud notifier is enabled but DORMALERT_WA_CLOUD_TOKEN, "
                "DORMALERT_WA_CLOUD_PHONE_NUMBER_ID and DORMALERT_WA_CLOUD_TO are required."
            )
        notifiers.append(
            MetaWhatsAppCloudNotifier(
                access_token=config.notification.wa_cloud_token,
                phone_number_id=config.notification.wa_cloud_phone_number_id,
                to=config.notification.wa_cloud_to,
                template_name=config.notification.wa_cloud_template,
                template_language=config.notification.wa_cloud_template_lang,
                timeout_seconds=config.notification.wa_cloud_timeout_seconds,
            )
        )

    if config.notification.telegram_enabled:
        if not (config.notification.telegram_bot_token and config.notification.telegram_chat_id):
            raise ValueError(
                "Telegram notifier is enabled but DORMALERT_TELEGRAM_BOT_TOKEN and "
                "DORMALERT_TELEGRAM_CHAT_ID are required."
            )
        notifiers.append(
            TelegramNotifier(
                bot_token=config.notification.telegram_bot_token,
                chat_id=config.notification.telegram_chat_id,
                timeout_seconds=config.notification.telegram_timeout_seconds,
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
