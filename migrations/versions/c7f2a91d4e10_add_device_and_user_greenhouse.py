"""add device and user_greenhouse tables for iot access and multi-tenancy

Revision ID: c7f2a91d4e10
Revises: 405c037bf903
Create Date: 2026-08-18 21:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7f2a91d4e10'
down_revision: Union[str, None] = '405c037bf903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IoT 设备注册表（MQTT / HTTP / UDP 接入凭证）
    op.create_table(
        'device',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_key', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('protocol', sa.String(length=20), nullable=False),
        sa.Column('greenhouse_id', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['greenhouse_id'], ['greenhouse.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_device_device_key', 'device', ['device_key'], unique=True)
    op.create_index('ix_device_greenhouse_id', 'device', ['greenhouse_id'], unique=False)

    # 用户-大棚多对多授权表
    op.create_table(
        'user_greenhouse',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('greenhouse_id', sa.Integer(), nullable=False),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['greenhouse_id'], ['greenhouse.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'greenhouse_id'),
    )


def downgrade() -> None:
    op.drop_table('user_greenhouse')
    op.drop_index('ix_device_greenhouse_id', table_name='device')
    op.drop_index('ix_device_device_key', table_name='device')
    op.drop_table('device')
