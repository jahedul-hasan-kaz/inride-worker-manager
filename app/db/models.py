from datetime import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class NotificationInDB(Base):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String, nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    email_log_id = Column(UUID(as_uuid=True), nullable=True)
    sms_log_id = Column(BigInteger, nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    from_ = Column("from", String, nullable=True)
    to = Column("to", String, nullable=True)
    thread_id = Column(String, nullable=True)
    preview_text = Column(Text, nullable=True)
    preview_title = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    is_read = Column(Boolean, default=False)
    push_status = Column(String, nullable=False, server_default=text("'pending'"))
    push_error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=text("NOW()"))
    updated_at = Column(DateTime, server_default=text("NOW()"))


class UserInDB(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    scope_tenant_id = Column(UUID(as_uuid=True), nullable=True)
    role = Column(String, nullable=False)
    is_disabled = Column(Boolean, default=False)
    is_notify_mobile = Column(Boolean, default=True)
    notification_priority = Column(Integer, nullable=False, server_default=text("0"))


class DeviceTokenInDB(Base):
    __tablename__ = "device_tokens"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=text("NOW()"))


class NotificationPushDeliveryInDB(Base):
    __tablename__ = "notification_push_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "device_token_id",
            name="uq_notification_push_deliveries_notif_device",
        ),
        {"schema": "public"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id = Column(UUID(as_uuid=True), nullable=False)
    device_token_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String, nullable=False, server_default=text("'sending'"))
    created_at = Column(DateTime, server_default=text("NOW()"))
    updated_at = Column(DateTime, server_default=text("NOW()"))
