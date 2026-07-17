from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class GlobalCompany(Base, TimestampMixin):
    """Cross-campaign enrichment cache, one row per company domain.

    Campaign `companies` rows cascade away when a campaign is deleted; this table
    survives, so re-running a campaign within the freshness window reuses the
    enrichment instead of re-scraping and re-paying the LLM. Expired rows are
    refreshed in place (upsert by domain) — never duplicated.
    """

    __tablename__ = "global_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    enrichment_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enrichment_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_till: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
