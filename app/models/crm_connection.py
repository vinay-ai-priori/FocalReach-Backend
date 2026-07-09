import enum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CRMProvider(str, enum.Enum):
    SALESFORCE = "salesforce"
    HUBSPOT = "hubspot"
    ZOHO = "zoho"
    DYNAMICS = "dynamics"
    PIPEDRIVE = "pipedrive"


class CRMConnection(Base, TimestampMixin):
    """Framework-only for MVP: stores which provider is 'connected' and its config."""

    __tablename__ = "crm_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[CRMProvider] = mapped_column(Enum(CRMProvider, name="crm_provider"), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_connected: Mapped[bool] = mapped_column(default=False, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
