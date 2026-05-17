"""initial check schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "land_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("cadastral_number", sa.String(50), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("purpose", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_land_checks_user_id", "land_checks", ["user_id"])
    op.create_index("ix_land_checks_status", "land_checks", ["status"])

    op.create_table(
        "check_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("check_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["check_id"], ["land_checks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_check_steps_check_id", "check_steps", ["check_id"])
    op.create_index("ix_check_steps_check_id_agent_name", "check_steps", ["check_id", "agent_name"], unique=True)

    op.create_table(
        "check_results",
        sa.Column("check_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plot_id", sa.String(50), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("legal_risk", sa.String(20), nullable=True),
        sa.Column(
            "stop_factors",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("best_scenario", sa.String(50), nullable=True),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.String(), nullable=True),
        sa.Column(
            "next_steps",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.ForeignKeyConstraint(["check_id"], ["land_checks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("check_results")
    op.drop_index("ix_check_steps_check_id_agent_name", table_name="check_steps")
    op.drop_index("ix_check_steps_check_id", table_name="check_steps")
    op.drop_table("check_steps")
    op.drop_index("ix_land_checks_status", table_name="land_checks")
    op.drop_index("ix_land_checks_user_id", table_name="land_checks")
    op.drop_table("land_checks")
