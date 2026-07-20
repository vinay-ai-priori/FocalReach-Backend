"""auth + rbac schema: tenants, organizations, users, refresh_tokens; org scoping

Revision ID: a1b2c3d4e5f6
Revises: 4964b4761fe9
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "4964b4761fe9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- new tables ----
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("criteria", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_org_tenant_name"),
    )
    sa.Enum("SUPER_ADMIN", "USER", name="user_role").create(op.get_bind(), checkfirst=True)
    # create_type=False: the type was created above; the table must not try to create it again
    user_role = ENUM("SUPER_ADMIN", "USER", name="user_role", create_type=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True, index=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(512), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="USER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ---- seed a default tenant/org and attach legacy data to it ----
    bind = op.get_bind()
    bind.exec_driver_sql(
        "INSERT INTO tenants (name, criteria) VALUES ('Default Tenant', '{}'::jsonb) ON CONFLICT (name) DO NOTHING"
    )
    tenant_id = bind.exec_driver_sql("SELECT id FROM tenants WHERE name = 'Default Tenant'").scalar()
    bind.exec_driver_sql(
        f"INSERT INTO organizations (tenant_id, name) VALUES ({tenant_id}, 'Default Org') ON CONFLICT DO NOTHING"
    )
    org_id = bind.exec_driver_sql(
        f"SELECT id FROM organizations WHERE tenant_id = {tenant_id} AND name = 'Default Org'"
    ).scalar()

    # ---- website_analyses: cache becomes per-organization ----
    op.add_column("website_analyses", sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_website_analyses_organization_id", "website_analyses", ["organization_id"])
    bind.exec_driver_sql(f"UPDATE website_analyses SET organization_id = {org_id}")
    op.drop_index("ix_website_analyses_domain", table_name="website_analyses")
    op.create_index("ix_website_analyses_domain", "website_analyses", ["domain"], unique=False)
    op.create_unique_constraint("uq_analysis_org_domain", "website_analyses", ["organization_id", "domain"])

    # ---- lead_imports: user ownership + org scoping; owner_key retired ----
    op.add_column("lead_imports", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("lead_imports", sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_lead_imports_user_id", "lead_imports", ["user_id"])
    op.create_index("ix_lead_imports_organization_id", "lead_imports", ["organization_id"])
    bind.exec_driver_sql(f"UPDATE lead_imports SET organization_id = {org_id}")
    op.drop_index("ix_lead_imports_owner_key", table_name="lead_imports")
    op.drop_column("lead_imports", "owner_key")

    # ---- icps: private to their creating user ----
    op.add_column("icps", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_icps_user_id", "icps", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_icps_user_id", table_name="icps")
    op.drop_column("icps", "user_id")

    op.add_column("lead_imports", sa.Column("owner_key", sa.String(320), nullable=True))
    op.create_index("ix_lead_imports_owner_key", "lead_imports", ["owner_key"])
    op.drop_index("ix_lead_imports_organization_id", table_name="lead_imports")
    op.drop_index("ix_lead_imports_user_id", table_name="lead_imports")
    op.drop_column("lead_imports", "organization_id")
    op.drop_column("lead_imports", "user_id")

    op.drop_constraint("uq_analysis_org_domain", "website_analyses")
    op.drop_index("ix_website_analyses_domain", table_name="website_analyses")
    op.create_index("ix_website_analyses_domain", "website_analyses", ["domain"], unique=True)
    op.drop_index("ix_website_analyses_organization_id", table_name="website_analyses")
    op.drop_column("website_analyses", "organization_id")

    op.drop_table("refresh_tokens")
    op.drop_table("users")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
    op.drop_table("organizations")
    op.drop_table("tenants")
