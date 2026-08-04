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
    direction = Column(String, nullable=True)
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


class DeviceTokenInDB(Base):
    __tablename__ = "device_tokens"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token = Column(String, nullable=False)
    platform = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=text("NOW()"))


class NotificationConfigInDB(Base):
    __tablename__ = "notification_config"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    is_enable = Column(Boolean, nullable=False, server_default=text("true"))
    ttl_sec = Column(Integer, nullable=False, server_default=text("60"))
    aggregation_type = Column(String, nullable=False, server_default=text("'none'"))
    aggregation_sec = Column(Integer, nullable=True)
    is_all_tenants = Column(Boolean, nullable=False, server_default=text("true"))
    is_in_flagged = Column(Boolean, nullable=False, server_default=text("false"))
    is_manual_sms = Column(Boolean, nullable=False, server_default=text("true"))
    is_manual_email = Column(Boolean, nullable=False, server_default=text("true"))
    is_sms_enable = Column(Boolean, nullable=False, server_default=text("true"))
    is_email_enable = Column(Boolean, nullable=False, server_default=text("true"))
    platform_os = Column(String, nullable=False, server_default=text("'both'"))


class NotificationTenantConfigInDB(Base):
    __tablename__ = "notification_tenants_config"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    is_in_flagged = Column(Boolean, nullable=False, server_default=text("false"))
    is_manual_sms = Column(Boolean, nullable=False, server_default=text("false"))
    is_manual_email = Column(Boolean, nullable=False, server_default=text("false"))
    is_sms_enable = Column(Boolean, nullable=False, server_default=text("false"))
    is_email_enable = Column(Boolean, nullable=False, server_default=text("false"))
    platform_os = Column(String, nullable=False, server_default=text("'both'"))
    priority = Column(String, nullable=False, server_default=text("'high'"))
    is_block = Column(Boolean, nullable=False, server_default=text("false"))


class SMSLogInDB(Base):
    __tablename__ = "sms_log"
    __table_args__ = {"schema": "public"}

    id = Column(BigInteger, primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    thread_key = Column(String, nullable=True)
    lead_id = Column(UUID(as_uuid=True), nullable=True)
    direction = Column(String, nullable=False)
    sent_by = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=True)


class EmailLogInDB(Base):
    __tablename__ = "email_log"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    lead_id = Column(UUID(as_uuid=True), nullable=True)
    direction = Column(String, nullable=False)
    in_reply_to_message_id = Column(String, nullable=True)
    sent_by = Column(Text, nullable=True)
    sender_user_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, nullable=True)


class LeadInDB(Base):
    __tablename__ = "leads"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    thread_id = Column(String, nullable=True)


class TenantInDB(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True)
    flag_level = Column(String, nullable=True)


class LeadUserFlagInDB(Base):
    __tablename__ = "lead_user_flags"
    __table_args__ = {"schema": "public"}

    lead_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    user_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    date_flagged = Column(DateTime, nullable=False, server_default=text("NOW()"))


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
