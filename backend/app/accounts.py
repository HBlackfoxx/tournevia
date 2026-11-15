from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Organization(Base):
    __tablename__ = 'organizations'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    # An administrator's choice to look addresses up with IGN's public service (app.geocoding).
    public_address_lookup: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    # The company's format for new delivery identifiers, and its lowest number (app.numbering).
    order_number_format: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_number_start: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    # Calculations plan every delivery this many minutes before its window ends (app.planning).
    safety_margin_minutes: Mapped[int] = mapped_column(Integer, default=15, server_default='15')


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (CheckConstraint("role IN ('admin','planner')", name='ck_users_role'),
                     CheckConstraint("language IN ('en','fr')", name='ck_users_language'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id', ondelete='RESTRICT'), index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(2), default='en', server_default='en')


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    __tablename__ = 'login_throttles'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
