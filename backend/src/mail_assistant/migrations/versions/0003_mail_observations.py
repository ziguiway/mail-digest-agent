"""Track observation identity separately from retained mail content."""

from alembic import op
import sqlalchemy as sa

revision = "0003_mail_observations"
down_revision = "0002_deliveries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mails",
        sa.Column("observed_state", sa.String(20), nullable=False, server_default="unknown"),
    )
    op.add_column("mails", sa.Column("source_epoch", sa.String(100), nullable=True))
    op.add_column("mails", sa.Column("last_observed_at", sa.String(40), nullable=True))
    op.create_index(
        "ix_mails_account_folder_state", "mails", ["account_id", "folder", "observed_state"]
    )


def downgrade():
    op.drop_index("ix_mails_account_folder_state", table_name="mails")
    with op.batch_alter_table("mails") as batch:
        batch.drop_column("last_observed_at")
        batch.drop_column("source_epoch")
        batch.drop_column("observed_state")
