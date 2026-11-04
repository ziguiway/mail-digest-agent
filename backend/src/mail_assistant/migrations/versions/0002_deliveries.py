"""Persist outbound messages independently from analysis tasks."""

from alembic import op
import sqlalchemy as sa

revision = "0002_deliveries"
down_revision = "0001_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("binding_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("recipient_chat_id", sa.String(200), nullable=False),
        sa.Column("reply_to", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("chunks", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("binding_id", "kind", "source_id"),
    )
    op.create_index("ix_deliveries_binding_id", "deliveries", ["binding_id"])
    op.create_index("ix_deliveries_status", "deliveries", ["status"])


def downgrade():
    op.drop_table("deliveries")
