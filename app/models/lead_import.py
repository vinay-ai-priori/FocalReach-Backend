import enum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class ImportStatus(str, enum.Enum):
    MAPPING = "mapping"  # uploaded, awaiting user confirmation of column mapping
    IMPORTED = "imported"
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    SCORING = "scoring"
    SCORED = "scored"
    FAILED = "failed"


class LeadImport(Base, PublicIDMixin, TimestampMixin):
    """A single CSV upload with its column mapping and validation report."""

    __tablename__ = "lead_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    icp_id: Mapped[int] = mapped_column(ForeignKey("icps.id", ondelete="CASCADE"), nullable=False, index=True)

    # Ownership: campaigns are private to their creating user; dedup and caching are
    # scoped to the organization.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Set only on PENDING re-uploads: a candidate dataset awaiting confirmation into the
    # campaign's permanent import. NULL on the permanent import itself.
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Fingerprint of the result-affecting ICP fields at the moment this import last ran.
    # Compared against the current ICP to detect "inputs changed" (stale results / re-run).
    icp_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), default=ImportStatus.MAPPING, nullable=False
    )
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    raw_columns: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    column_mapping: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # canonical -> csv column
    # canonical -> {confidence, source: exact|fuzzy|semantic|manual} for the mapping UI
    mapping_meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    missing_fields: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # validation report
    raw_rows: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # kept until import confirmed
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    icp = relationship("ICP", back_populates="lead_imports")
    companies = relationship("Company", back_populates="lead_import", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="lead_import", cascade="all, delete-orphan")
