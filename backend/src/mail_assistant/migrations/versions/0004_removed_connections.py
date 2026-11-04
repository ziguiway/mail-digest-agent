"""Retain source identities after disconnecting an account."""

from alembic import op
import sqlalchemy as sa

revision = "0004_removed_connections"
down_revision = "0003_mail_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("removed_at", sa.String(40), nullable=True))


def downgrade():
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("removed_at")
