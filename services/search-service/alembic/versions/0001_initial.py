"""initial search schema

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
        "land_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("query", sa.String(), nullable=False, server_default=""),
        sa.Column("user_profile_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_land_searches_user_id", "land_searches", ["user_id"])
    op.create_index("ix_land_searches_status", "land_searches", ["status"])

    op.create_table(
        "search_criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("criteria_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["search_id"], ["land_searches.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_search_criteria_search_id", "search_criteria", ["search_id"], unique=True)

    op.create_table(
        "search_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["search_id"], ["land_searches.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_search_steps_search_id", "search_steps", ["search_id"])
    op.create_index("ix_search_steps_search_id_agent_name", "search_steps", ["search_id", "agent_name"], unique=True)

    op.create_table(
        "search_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plot_id", sa.String(50), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scores_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plot_summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["search_id"], ["land_searches.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_search_candidates_search_id", "search_candidates", ["search_id"])
    op.create_index("ix_search_candidates_search_id_plot_id", "search_candidates", ["search_id", "plot_id"], unique=True)

    op.create_table(
        "search_recommendations",
        sa.Column("search_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "top_plot_ids",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("explanation", sa.String(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["search_id"], ["land_searches.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("search_recommendations")
    op.drop_index("ix_search_candidates_search_id_plot_id", table_name="search_candidates")
    op.drop_index("ix_search_candidates_search_id", table_name="search_candidates")
    op.drop_table("search_candidates")
    op.drop_index("ix_search_steps_search_id_agent_name", table_name="search_steps")
    op.drop_index("ix_search_steps_search_id", table_name="search_steps")
    op.drop_table("search_steps")
    op.drop_index("ix_search_criteria_search_id", table_name="search_criteria")
    op.drop_table("search_criteria")
    op.drop_index("ix_land_searches_status", table_name="land_searches")
    op.drop_index("ix_land_searches_user_id", table_name="land_searches")
    op.drop_table("land_searches")
