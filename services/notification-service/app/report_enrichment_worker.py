"""Report Enrichment Worker Process (Feature 10).

Tiến trình RabbitMQ độc lập lắng nghe sự kiện `report.created` trên queue `q.report.enrichment`
để thực hiện các tác vụ nặng ở chế độ nền (không làm chậm HTTP request của người dùng):
- Phân tích ngữ cảnh lỗi (context) để tự động nâng mức độ ưu tiên (`priority`) nếu phát hiện
  lỗi nghiêm trọng (ví dụ crash, mất dữ liệu, lỗi 5xx liên tiếp).
- Sinh thông tin tóm tắt chẩn đoán nhanh (`diagnostic_summary`) hỗ trợ Admin xử lý nhanh hơn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict

from common_auth.events import consume_forever

from app.core.notification_store import ReportRecord, get_session

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [ReportEnrichment] %(message)s",
)
logger = logging.getLogger("ReportEnrichment")

QUEUE_REPORT_ENRICHMENT = "q.report.enrichment"


def enrich_report_sync(report_id: str) -> None:
    with get_session() as session:
        record = session.get(ReportRecord, str(report_id))
        if not record:
            return
        try:
            ctx = json.loads(record.context_json) if record.context_json else {}
        except Exception:
            ctx = {}

        changed = False
        http_status = ctx.get("http_status")
        if isinstance(http_status, int) and http_status >= 500 and record.priority in ("low", "normal"):
            record.priority = "high"
            changed = True

        if "diagnostic_summary" not in ctx and record.kind == "quick_error":
            ctx["diagnostic_summary"] = (
                f"Feature={record.feature_code or 'N/A'} | "
                f"HTTP={ctx.get('http_status', 'N/A')} | "
                f"Error={ctx.get('error_code', 'N/A')} | "
                f"ReqID={ctx.get('request_id', 'N/A')}"
            )
            record.context_json = json.dumps(ctx, ensure_ascii=False, default=str)
            changed = True

        if changed:
            session.commit()


async def handle_enrichment_event(routing_key: str, event: Dict[str, Any]) -> None:
    payload = event.get("payload") or {}
    report_id = payload.get("report_id")
    if report_id:
        await asyncio.to_thread(enrich_report_sync, str(report_id))
        logger.info("[ENRICHMENT] Đã làm giàu dữ liệu ngầm cho report_id=%s", report_id)


async def start_enrichment_worker() -> None:
    await consume_forever(
        QUEUE_REPORT_ENRICHMENT,
        ["report.created"],
        handle_enrichment_event,
        prefetch=5,
    )


if __name__ == "__main__":
    try:
        asyncio.run(start_enrichment_worker())
    except KeyboardInterrupt:
        logger.info("Đã dừng Report Enrichment Worker.")
        sys.exit(0)
