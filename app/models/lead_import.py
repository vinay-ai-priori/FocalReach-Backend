import enum

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, UniqueConstraint, text
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


class ImportKind(str, enum.Enum):
    PRIMARY = "primary"  # the campaign's permanent import (at most one per campaign)
    PENDING_REUPLOAD = "pending_reupload"  # candidate re-upload awaiting confirmation


class LeadImport(Base, PublicIDMixin, TimestampMixin):
    """A single CSV upload with its column mapping and validation report.

    Ownership is a single path: lead_import → campaign → user → organization.
    No duplicated user/org/icp columns — the campaign is the source of truth."""

    __tablename__ = "lead_imports"
    __table_args__ = (
        # One permanent (PRIMARY) import per campaign, enforced at the DB level; a
        # campaign can hold any number of PENDING_REUPLOAD candidates over its life.
        Index(
            "ux_lead_imports_campaign_primary",
            "campaign_id",
            unique=True,
            postgresql_where=text("kind = 'PRIMARY'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ImportKind] = mapped_column(
        Enum(ImportKind, name="import_kind"), default=ImportKind.PRIMARY, nullable=False
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
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Stage-2 enrichment progress (qualify_import, gate-passers only). NULL until stage 1
    # finishes; enrichment_done is bumped once per wave commit.
    enrichment_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enrichment_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    campaign = relationship("Campaign", back_populates="lead_imports")
    rows = relationship("LeadImportRow", back_populates="lead_import", cascade="all, delete-orphan")
    company_qualifications = relationship(
        "CompanyQualification", back_populates="lead_import", cascade="all, delete-orphan"
    )
    leads = relationship("Lead", back_populates="lead_import", cascade="all, delete-orphan")


class LeadImportRow(Base, TimestampMixin):
    """Raw CSV rows staged during column-mapping, one row per line. Kept out of
    lead_imports so listing imports never loads megabytes of JSONB; purged once the
    import is confirmed and parsed into companies/leads."""

    __tablename__ = "lead_import_rows"
    __table_args__ = (UniqueConstraint("lead_import_id", "row_number", name="uq_import_row_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_import_id: Mapped[int] = mapped_column(
        ForeignKey("lead_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    lead_import = relationship("LeadImport", back_populates="rows")
