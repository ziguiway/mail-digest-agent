"""Refreshable 24-hour digests, immutable revisions and per-mail analysis cache."""

from alembic import op
import sqlalchemy as sa

revision = "0005_digest_refresh"
down_revision = "0004_removed_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "digest_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("latest_digest_id", sa.String(40), nullable=True),
        sa.Column("active_task_id", sa.String(40), nullable=True),
        sa.Column("checked_at", sa.String(40), nullable=True),
        sa.Column("checked_start", sa.String(40), nullable=True),
        sa.Column("checked_end", sa.String(40), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "mail_analysis",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mail_id", sa.String(80), sa.ForeignKey("mails.id"), nullable=False),
        sa.Column("item", sa.JSON(), nullable=False),
        sa.Column("valid_until", sa.String(40), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_mail_analysis_mail_id", "mail_analysis", ["mail_id"])
    with op.batch_alter_table("digests") as batch:
        batch.add_column(sa.Column("group_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("legacy", sa.Boolean(), server_default="1", nullable=False))
        batch.add_column(sa.Column("range_start", sa.String(40), nullable=True))
        batch.add_column(sa.Column("range_end", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column("timezone", sa.String(80), server_default="Asia/Shanghai", nullable=False)
        )
        batch.add_column(
            sa.Column("source_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("sources", sa.JSON(), server_default="[]", nullable=False))
        batch.create_foreign_key("fk_digests_group", "digest_groups", ["group_id"], ["id"])
        batch.create_unique_constraint("uq_digests_group_revision", ["group_id", "revision"])


def downgrade():
    with op.batch_alter_table("digests") as batch:
        batch.drop_constraint("uq_digests_group_revision", type_="unique")
        batch.drop_constraint("fk_digests_group", type_="foreignkey")
        for name in (
            "sources",
            "fingerprint",
            "source_count",
            "timezone",
            "range_end",
            "range_start",
            "legacy",
            "revision",
            "group_id",
        ):
            batch.drop_column(name)
    op.drop_table("mail_analysis")
    op.drop_table("digest_groups")
