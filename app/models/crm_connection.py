import enum

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
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
    """Framework-only for MVP: stores which provider is 'connected' and its config.
    Org-scoped — one org connecting a provider must never mark it connected for
    every other org."""

    __tablename__ = "crm_connections"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider", name="uq_crm_org_provider", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = the super admin's own connection (outside every organization).
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[CRMProvider] = mapped_column(Enum(CRMProvider, name="crm_provider"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_connected: Mapped[bool] = mapped_column(default=False, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
