"""Persist query tables alongside the complete plain-text message."""

from alembic import op
import sqlalchemy as sa

revision = "0006_query_tables"
down_revision = "0005_digest_refresh"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("presentation", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("presentation")
