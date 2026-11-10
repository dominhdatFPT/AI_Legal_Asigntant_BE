"""ORM Models & Seed Data cho chức năng Report (Ticket & Quick Error) và Thông báo nhóm (Broadcast)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


def register_report_models(Base: type[DeclarativeBase]):
    """Đăng ký các model Report & Group Notification vào cùng metadata của NotificationStore."""

    class ReportCategoryRecord(Base):
        __tablename__ = "report_categories"

        code = Column(String(40), primary_key=True)
        name_vi = Column(String(120), nullable=False)
        description = Column(Text, nullable=True)
        allowed_roles = Column(String(64), nullable=False, default="citizen,lawyer")
        sort_order = Column(Integer, nullable=False, default=0)
        is_active = Column(Boolean, nullable=False, default=True)

        def to_dict(self) -> Dict[str, Any]:
            return {
                "code": self.code,
                "name_vi": self.name_vi,
                "description": self.description,
                "allowed_roles": [r.strip() for r in (self.allowed_roles or "").split(",") if r.strip()],
                "sort_order": self.sort_order,
                "is_active": self.is_active,
            }

    class ReportReasonRecord(Base):
        __tablename__ = "report_reasons"

        code = Column(String(40), primary_key=True)
        category_code = Column(String(40), ForeignKey("report_categories.code"), nullable=True)
        kind = Column(String(16), nullable=False)  # 'ticket' | 'quick_error'
        name_vi = Column(String(160), nullable=False)
        allowed_roles = Column(String(64), nullable=False, default="citizen,lawyer")
        default_priority = Column(String(10), nullable=False, default="normal")
        requires_target = Column(Boolean, nullable=False, default=False)
        requires_note = Column(Boolean, nullable=False, default=False)
        sort_order = Column(Integer, nullable=False, default=0)
        is_active = Column(Boolean, nullable=False, default=True)

        def to_dict(self) -> Dict[str, Any]:
            return {
                "code": self.code,
                "category_code": self.category_code,
                "kind": self.kind,
                "name_vi": self.name_vi,
                "allowed_roles": [r.strip() for r in (self.allowed_roles or "").split(",") if r.strip()],
                "default_priority": self.default_priority,
                "requires_target": self.requires_target,
                "requires_note": self.requires_note,
                "sort_order": self.sort_order,
                "is_active": self.is_active,
            }

    class ReportRecord(Base):
        __tablename__ = "reports"

        id = Column(String(64), primary_key=True)
        code = Column(String(24), unique=True, index=True, nullable=False)
        kind = Column(String(16), nullable=False)  # 'ticket' | 'quick_error'
        reporter_id = Column(String(64), index=True, nullable=False)
        reporter_role = Column(String(16), nullable=False)  # 'citizen' | 'lawyer'
        category_code = Column(String(40), nullable=True, index=True)
        reason_code = Column(String(40), nullable=False)
        title = Column(String(200), nullable=False)
        description = Column(Text, nullable=True)
        priority = Column(String(10), nullable=False, default="normal")
        status = Column(String(16), nullable=False, default="pending", index=True)
        feature_code = Column(String(40), nullable=True)
        target_type = Column(String(40), nullable=True)
        target_id = Column(String(64), nullable=True)
        context_json = Column(Text, nullable=False, default="{}")
        fingerprint = Column(String(64), index=True, nullable=True)
        occurrence_count = Column(Integer, nullable=False, default=1)
        duplicate_of = Column(String(64), nullable=True)
        assignee_id = Column(String(64), nullable=True, index=True)
        reject_reason_code = Column(String(40), nullable=True)
        decision_note = Column(Text, nullable=True)
        decided_by = Column(String(64), nullable=True)
        decided_at = Column(DateTime(timezone=True), nullable=True)
        resolved_at = Column(DateTime(timezone=True), nullable=True)
        version = Column(Integer, nullable=False, default=1)
        created_at = Column(DateTime(timezone=True), nullable=False)
        updated_at = Column(DateTime(timezone=True), nullable=False)

        def to_dict(self, include_context: bool = True) -> Dict[str, Any]:
            try:
                ctx = json.loads(self.context_json) if (include_context and self.context_json) else {}
            except Exception:
                ctx = {}
            data = {
                "id": self.id,
                "code": self.code,
                "kind": self.kind,
                "reporter_id": self.reporter_id,
                "reporter_role": self.reporter_role,
                "category_code": self.category_code,
                "reason_code": self.reason_code,
                "title": self.title,
                "description": self.description,
                "priority": self.priority,
                "status": self.status,
                "feature_code": self.feature_code,
                "target_type": self.target_type,
                "target_id": self.target_id,
                "occurrence_count": self.occurrence_count,
                "duplicate_of": self.duplicate_of,
                "assignee_id": self.assignee_id,
                "reject_reason_code": self.reject_reason_code,
                "decision_note": self.decision_note,
                "decided_by": self.decided_by,
                "decided_at": self.decided_at.isoformat() if self.decided_at else None,
                "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
                "version": self.version,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            }
            if include_context:
                data["context"] = ctx
            return data

    class ReportAttachmentRecord(Base):
        __tablename__ = "report_attachments"

        id = Column(String(64), primary_key=True)
        report_id = Column(String(64), index=True, nullable=True)
        uploader_id = Column(String(64), nullable=False)
        storage_key = Column(String(512), nullable=False)
        thumb_key = Column(String(512), nullable=True)
        file_name = Column(String(255), nullable=False)
        mime_type = Column(String(64), nullable=False)
        size_bytes = Column(Integer, nullable=False)
        created_at = Column(DateTime(timezone=True), nullable=False)

        def to_dict(self) -> Dict[str, Any]:
            return {
                "id": self.id,
                "report_id": self.report_id,
                "uploader_id": self.uploader_id,
                "file_name": self.file_name,
                "mime_type": self.mime_type,
                "size_bytes": self.size_bytes,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            }

    class ReportStatusHistoryRecord(Base):
        __tablename__ = "report_status_history"

        id = Column(Integer, primary_key=True, autoincrement=True)
        report_id = Column(String(64), index=True, nullable=False)
        from_status = Column(String(16), nullable=True)
        to_status = Column(String(16), nullable=False)
        actor_id = Column(String(64), nullable=False)
        actor_role = Column(String(16), nullable=False)
        note = Column(Text, nullable=True)
        created_at = Column(DateTime(timezone=True), nullable=False)

        def to_dict(self) -> Dict[str, Any]:
            return {
                "id": self.id,
                "report_id": self.report_id,
                "from_status": self.from_status,
                "to_status": self.to_status,
                "actor_id": self.actor_id,
                "actor_role": self.actor_role,
                "note": self.note,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            }

    class GroupNotificationRecord(Base):
        __tablename__ = "group_notifications"

        id = Column(String(64), primary_key=True)
        audience = Column(String(16), index=True, nullable=False)  # 'all' | 'admin' | 'lawyer' | 'citizen'
        event_type = Column(String(64), nullable=False)
        category = Column(String(24), nullable=False, default="system")
        priority = Column(String(10), nullable=False, default="normal")
        title = Column(String(255), nullable=False)
        message = Column(Text, nullable=False)
        icon = Column(String(64), nullable=False, default="bell")
        action_url = Column(String(512), nullable=False, default="/")
        source_type = Column(String(24), nullable=True)
        source_id = Column(String(64), nullable=True)
        created_by = Column(String(64), nullable=True)
        created_at = Column(DateTime(timezone=True), nullable=False)
        expires_at = Column(DateTime(timezone=True), nullable=True)
        is_deleted = Column(Boolean, nullable=False, default=False)

        def to_dict(self, is_read: bool = False) -> Dict[str, Any]:
            return {
                "id": self.id,
                "scope": "group",
                "audience": self.audience,
                "event_type": self.event_type,
                "category": self.category,
                "priority": self.priority,
                "title": self.title,
                "message": self.message,
                "icon": self.icon,
                "action_url": self.action_url,
                "source_type": self.source_type,
                "source_id": self.source_id,
                "is_read": is_read,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            }

    class GroupNotificationReadRecord(Base):
        __tablename__ = "group_notification_reads"

        group_notification_id = Column(String(64), primary_key=True)
        user_id = Column(String(64), primary_key=True, index=True)
        is_read = Column(Boolean, nullable=False, default=True)
        read_at = Column(DateTime(timezone=True), nullable=False)
        hidden = Column(Boolean, nullable=False, default=False)

    class BroadcastRecord(Base):
        __tablename__ = "broadcasts"

        id = Column(String(64), primary_key=True)
        title = Column(String(150), nullable=False)
        content = Column(Text, nullable=False)
        audience = Column(String(16), nullable=False, default="all")
        priority = Column(String(10), nullable=False, default="normal")
        channels = Column(String(120), nullable=False, default="in_app")
        action_url = Column(String(512), nullable=False, default="/")
        status = Column(String(16), nullable=False, default="sent", index=True)
        scheduled_at = Column(DateTime(timezone=True), nullable=True)
        sent_at = Column(DateTime(timezone=True), nullable=True)
        expires_at = Column(DateTime(timezone=True), nullable=True)
        group_notification_id = Column(String(64), nullable=True)
        stats_json = Column(Text, nullable=False, default="{}")
        created_by = Column(String(64), nullable=False)
        created_at = Column(DateTime(timezone=True), nullable=False)

        def to_dict(self) -> Dict[str, Any]:
            try:
                stats = json.loads(self.stats_json) if self.stats_json else {}
            except Exception:
                stats = {}
            return {
                "id": self.id,
                "title": self.title,
                "content": self.content,
                "audience": self.audience,
                "priority": self.priority,
                "channels": [c.strip() for c in (self.channels or "").split(",") if c.strip()],
                "action_url": self.action_url,
                "status": self.status,
                "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
                "sent_at": self.sent_at.isoformat() if self.sent_at else None,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "group_notification_id": self.group_notification_id,
                "stats": stats,
                "created_by": self.created_by,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            }

    return {
        "ReportCategoryRecord": ReportCategoryRecord,
        "ReportReasonRecord": ReportReasonRecord,
        "ReportRecord": ReportRecord,
        "ReportAttachmentRecord": ReportAttachmentRecord,
        "ReportStatusHistoryRecord": ReportStatusHistoryRecord,
        "GroupNotificationRecord": GroupNotificationRecord,
        "GroupNotificationReadRecord": GroupNotificationReadRecord,
        "BroadcastRecord": BroadcastRecord,
    }


DEFAULT_CATEGORIES: List[Dict[str, Any]] = [
    {"code": "ACCOUNT", "name_vi": "Tài khoản & đăng nhập", "allowed_roles": "citizen,lawyer", "sort_order": 10},
    {"code": "AI_ASSISTANT", "name_vi": "Trợ lý AI (Chatbox)", "allowed_roles": "citizen,lawyer", "sort_order": 20},
    {"code": "PROCEDURE", "name_vi": "Thủ tục hành chính & nộp hồ sơ", "allowed_roles": "citizen,lawyer", "sort_order": 30},
    {"code": "CONTRACT", "name_vi": "Hợp đồng & biểu mẫu", "allowed_roles": "citizen,lawyer", "sort_order": 40},
    {"code": "DOCUMENT", "name_vi": "Tài liệu tải lên", "allowed_roles": "citizen,lawyer", "sort_order": 50},
    {"code": "CONSULTATION", "name_vi": "Phiên tư vấn với luật sư", "allowed_roles": "citizen,lawyer", "sort_order": 60},
    {"code": "LAWYER_PROGRAM", "name_vi": "Hồ sơ luật sư", "allowed_roles": "lawyer", "sort_order": 70},
    {"code": "LOCATION", "name_vi": "Tra cứu nơi nộp hồ sơ", "allowed_roles": "citizen,lawyer", "sort_order": 80},
    {"code": "SECURITY", "name_vi": "Bảo mật & quyền riêng tư", "allowed_roles": "citizen,lawyer", "sort_order": 90},
    {"code": "SUGGESTION", "name_vi": "Góp ý & đề xuất", "allowed_roles": "citizen,lawyer", "sort_order": 100},
    {"code": "OTHER", "name_vi": "Khác", "allowed_roles": "citizen,lawyer", "sort_order": 999},
]

DEFAULT_REASONS: List[Dict[str, Any]] = [
    # Ticket reasons - ACCOUNT
    {"code": "ACC_LOGIN_FAILED", "category_code": "ACCOUNT", "kind": "ticket", "name_vi": "Không đăng nhập được / không nhận OTP", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "ACC_CCCD_VERIFY", "category_code": "ACCOUNT", "kind": "ticket", "name_vi": "Xác thực CCCD sai / bị từ chối", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "ACC_LOCKED_UNFAIR", "category_code": "ACCOUNT", "kind": "ticket", "name_vi": "Tài khoản bị khóa không rõ lý do", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 3},
    {"code": "ACC_PROFILE_WRONG", "category_code": "ACCOUNT", "kind": "ticket", "name_vi": "Thông tin hồ sơ cá nhân hiển thị sai", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 4},
    # Ticket reasons - AI_ASSISTANT
    {"code": "AI_WRONG_ANSWER", "category_code": "AI_ASSISTANT", "kind": "ticket", "name_vi": "Trả lời sai nội dung pháp lý", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "AI_OUTDATED_LAW", "category_code": "AI_ASSISTANT", "kind": "ticket", "name_vi": "Trích dẫn luật đã hết hiệu lực", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "AI_HARMFUL", "category_code": "AI_ASSISTANT", "kind": "ticket", "name_vi": "Nội dung không phù hợp / độc hại", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 3},
    {"code": "AI_NO_RESPONSE", "category_code": "AI_ASSISTANT", "kind": "ticket", "name_vi": "Không phản hồi / trả lời bị cắt", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 4},
    # Ticket reasons - PROCEDURE
    {"code": "PRO_FORM_TEMPLATE_WRONG", "category_code": "PROCEDURE", "kind": "ticket", "name_vi": "Mẫu đơn sai quy định / sai phiên bản", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "PRO_MISSING_FIELD", "category_code": "PROCEDURE", "kind": "ticket", "name_vi": "Thiếu trường thông tin cần thiết", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "PRO_SUBMIT_FAILED", "category_code": "PROCEDURE", "kind": "ticket", "name_vi": "Không nộp được hồ sơ", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 3},
    {"code": "PRO_DOSSIER_PDF_WRONG", "category_code": "PROCEDURE", "kind": "ticket", "name_vi": "File PDF / mã QR hồ sơ bị sai", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 4},
    # Ticket reasons - CONTRACT
    {"code": "CON_TEMPLATE_WRONG", "category_code": "CONTRACT", "kind": "ticket", "name_vi": "Nội dung mẫu hợp đồng sai", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "CON_EXPORT_FAILED", "category_code": "CONTRACT", "kind": "ticket", "name_vi": "Xuất file DOCX/PDF lỗi", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "CON_FORMAT_BROKEN", "category_code": "CONTRACT", "kind": "ticket", "name_vi": "Định dạng / font chữ bị vỡ", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 3},
    # Ticket reasons - DOCUMENT
    {"code": "DOC_UPLOAD_FAILED", "category_code": "DOCUMENT", "kind": "ticket", "name_vi": "Không tải lên được", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "DOC_OCR_WRONG", "category_code": "DOCUMENT", "kind": "ticket", "name_vi": "Trích xuất (OCR) sai dữ liệu", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    # Ticket reasons - CONSULTATION
    {"code": "CSL_LAWYER_CONDUCT", "category_code": "CONSULTATION", "kind": "ticket", "name_vi": "Luật sư có thái độ / hành vi không đúng", "allowed_roles": "citizen", "default_priority": "high", "requires_target": True, "requires_note": False, "sort_order": 1},
    {"code": "CSL_CLIENT_CONDUCT", "category_code": "CONSULTATION", "kind": "ticket", "name_vi": "Người dùng có hành vi xúc phạm / quấy rối", "allowed_roles": "lawyer", "default_priority": "high", "requires_target": True, "requires_note": False, "sort_order": 2},
    {"code": "CSL_OFF_PLATFORM_PAYMENT", "category_code": "CONSULTATION", "kind": "ticket", "name_vi": "Yêu cầu giao dịch/chuyển khoản ngoài nền tảng", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": True, "requires_note": False, "sort_order": 3},
    {"code": "CSL_SPAM", "category_code": "CONSULTATION", "kind": "ticket", "name_vi": "Spam / quảng cáo", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": True, "requires_note": False, "sort_order": 4},
    {"code": "CSL_CONNECTION", "category_code": "CONSULTATION", "kind": "ticket", "name_vi": "Lỗi kết nối, mất tin nhắn", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 5},
    # Ticket reasons - LAWYER_PROGRAM
    {"code": "LAW_REVIEW_DELAY", "category_code": "LAWYER_PROGRAM", "kind": "ticket", "name_vi": "Hồ sơ đăng ký chậm được duyệt", "allowed_roles": "lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "LAW_PROFILE_WRONG", "category_code": "LAWYER_PROGRAM", "kind": "ticket", "name_vi": "Thông tin chứng chỉ / hồ sơ hiển thị sai", "allowed_roles": "lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "LAW_CASE_ASSIGNMENT", "category_code": "LAWYER_PROGRAM", "kind": "ticket", "name_vi": "Lỗi nhận / phân công yêu cầu tư vấn", "allowed_roles": "lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 3},
    # Ticket reasons - LOCATION
    {"code": "LOC_WRONG_ADDRESS", "category_code": "LOCATION", "kind": "ticket", "name_vi": "Sai địa chỉ / giờ làm việc cơ quan", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 1},
    # Ticket reasons - SECURITY
    {"code": "SEC_DATA_LEAK", "category_code": "SECURITY", "kind": "ticket", "name_vi": "Thấy dữ liệu của người khác", "allowed_roles": "citizen,lawyer", "default_priority": "critical", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "SEC_VULNERABILITY", "category_code": "SECURITY", "kind": "ticket", "name_vi": "Phát hiện lỗ hổng bảo mật", "allowed_roles": "citizen,lawyer", "default_priority": "critical", "requires_target": False, "requires_note": False, "sort_order": 2},
    # Ticket reasons - SUGGESTION
    {"code": "SUG_FEATURE", "category_code": "SUGGESTION", "kind": "ticket", "name_vi": "Đề xuất tính năng mới", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "SUG_UX", "category_code": "SUGGESTION", "kind": "ticket", "name_vi": "Góp ý giao diện / trải nghiệm", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 2},
    # Ticket reasons - OTHER
    {"code": "OTH_OTHER", "category_code": "OTHER", "kind": "ticket", "name_vi": "Vấn đề khác", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": True, "sort_order": 1},
    # Quick Error chips
    {"code": "QE_CANNOT_LOAD", "category_code": None, "kind": "quick_error", "name_vi": "Không tải được trang / dữ liệu", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 1},
    {"code": "QE_NO_RESPONSE", "category_code": None, "kind": "quick_error", "name_vi": "Bấm nút không phản hồi", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 2},
    {"code": "QE_WRONG_RESULT", "category_code": None, "kind": "quick_error", "name_vi": "Kết quả hiển thị sai", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 3},
    {"code": "QE_SAVE_FAILED", "category_code": None, "kind": "quick_error", "name_vi": "Lưu / gửi không thành công", "allowed_roles": "citizen,lawyer", "default_priority": "high", "requires_target": False, "requires_note": False, "sort_order": 4},
    {"code": "QE_FILE_ERROR", "category_code": None, "kind": "quick_error", "name_vi": "Tải lên / tải xuống file lỗi", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 5},
    {"code": "QE_AI_ERROR", "category_code": None, "kind": "quick_error", "name_vi": "AI trả lời sai / không trả lời", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": False, "sort_order": 6},
    {"code": "QE_UI_BROKEN", "category_code": None, "kind": "quick_error", "name_vi": "Giao diện bị vỡ / hiển thị lỗi", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 7},
    {"code": "QE_TOO_SLOW", "category_code": None, "kind": "quick_error", "name_vi": "Quá chậm", "allowed_roles": "citizen,lawyer", "default_priority": "low", "requires_target": False, "requires_note": False, "sort_order": 8},
    {"code": "QE_OTHER", "category_code": None, "kind": "quick_error", "name_vi": "Lỗi khác", "allowed_roles": "citizen,lawyer", "default_priority": "normal", "requires_target": False, "requires_note": True, "sort_order": 9},
]

REJECT_REASON_CATALOG: Dict[str, str] = {
    "REJ_DUPLICATE": "Trùng với báo cáo đã có",
    "REJ_NOT_REPRODUCIBLE": "Không tái hiện được lỗi",
    "REJ_EXPECTED_BEHAVIOR": "Hệ thống hoạt động đúng thiết kế",
    "REJ_INSUFFICIENT_INFO": "Thiếu thông tin, vui lòng gửi lại chi tiết hơn",
    "REJ_OUT_OF_SCOPE": "Ngoài phạm vi hỗ trợ của nền tảng",
    "REJ_ABUSE": "Nội dung spam / lạm dụng chức năng báo cáo",
}
