"""Static IMAP/SMTP presets per provider — the user only ever supplies their email
address and app password; host/port are never user-entered (avoids typos and is how
Instantly/similar tools do it for the two providers that matter for cold outreach)."""

from dataclasses import dataclass

from app.models.mailbox_connection import MailboxProvider


@dataclass(frozen=True)
class ProviderPreset:
    provider: MailboxProvider
    display_name: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    app_password_url: str
    instructions: list[str]


PROVIDER_PRESETS: dict[MailboxProvider, ProviderPreset] = {
    MailboxProvider.GOOGLE: ProviderPreset(
        provider=MailboxProvider.GOOGLE,
        display_name="Google (Gmail / Workspace)",
        imap_host="imap.gmail.com",
        imap_port=993,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        app_password_url="https://myaccount.google.com/apppasswords",
        instructions=[
            "Turn on 2-Step Verification for your Google account, if it isn't already (App Passwords require it).",
            "Go to Google Account -> Security -> App passwords (or use the link below).",
            "Enter a name for the app (e.g. \"Focal Reach\") and click Create.",
            "Google shows a 16-character app password — copy it. This is NOT your normal Google password.",
            "Come back here and paste it in, along with the Gmail/Workspace address it belongs to.",
        ],
    ),
    MailboxProvider.MICROSOFT: ProviderPreset(
        provider=MailboxProvider.MICROSOFT,
        display_name="Microsoft (Outlook / Microsoft 365)",
        imap_host="outlook.office365.com",
        imap_port=993,
        smtp_host="smtp.office365.com",
        smtp_port=587,
        app_password_url="https://account.live.com/proofs/AppPassword",
        instructions=[
            "Turn on two-step verification for your Microsoft account, if it isn't already (required for app passwords).",
            "Go to Microsoft account -> Security -> Advanced security options -> App passwords (or use the link below).",
            "Click Create a new app password. Microsoft generates one and shows it once — copy it.",
            "If your account is managed by a work/school organization and you don't see this option, ask your admin to enable app passwords (or use \"modern auth\" mailbox support, coming later).",
            "Come back here and paste the app password in, along with your Outlook/Microsoft 365 address.",
        ],
    ),
}


def get_preset(provider: MailboxProvider) -> ProviderPreset:
    return PROVIDER_PRESETS[provider]
