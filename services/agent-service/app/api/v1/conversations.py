import asyncio
import contextlib
import inspect
import json
import logging
import hashlib
import math
import threading
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Literal

from anyio import CancelScope
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, raw_access_token
from app.core.auth import TokenPayload
from app.core.config import get_settings
from app.core.database import get_db
from app.models import Conversation
from app.repositories import conversation as repo
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationOut,
    Pagination,
)
from app.schemas.message import MessageOut, SendMessageRequest, SendMessageResponse
from app.schemas.transcript import Transcript, TranscriptMessage, TranscriptTurn
from app.services import (
    admission, cache, case_budget, agent, conversation_cleanup, document_client, history_cache, llm_client, quota, rag_client, responder,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent/conversations", tags=["conversations"])

def _request_id(x_request_id: str | None) -> str:
    return x_request_id or f"req-{uuid.uuid4()}"


def _ok(data, request_id: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"data": data, "error": None, "request_id": request_id}),
        headers={"X-Request-ID": request_id},
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _user_id(user: TokenPayload) -> uuid.UUID:
    try:
        return uuid.UUID(user.sub)
    except ValueError:
        raise _error(401, "UNAUTHORIZED", "Token không hợp lệ.")


def _owned_conversation(db: Session, conversation_id: uuid.UUID, user: TokenPayload) -> Conversation:
    conversation = repo.get_conversation(db, conversation_id)
    if conversation is None or repo.is_expired(conversation, conversation_cleanup.retention_cutoff()):
        if conversation is not None and conversation.user_id == _user_id(user):
            history_cache.invalidate(conversation.user_id, conversation.id)
        raise _error(404, "CONVERSATION_NOT_FOUND", "Không tìm thấy hội thoại.")
    if conversation.user_id != _user_id(user):
        raise _error(403, "CONVERSATION_FORBIDDEN", "Bạn không có quyền truy cập hội thoại này.")
    return conversation


@router.post("")
def create_conversation(
    payload: ConversationCreate | None = None,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    title = payload.title if payload and payload.title else None
    conversation = repo.create_conversation(db, _user_id(user), title)
    return _ok(ConversationOut.model_validate(conversation), _request_id(x_request_id), 201)


@router.get("")
def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    items, total = repo.list_conversations(
        db, _user_id(user), page, page_size, cutoff=conversation_cleanup.retention_cutoff(),
    )
    data = ConversationList(
        items=[ConversationOut.model_validate(c) for c in items],
        pagination=Pagination(
            page=page, page_size=page_size, total=total,
            total_pages=math.ceil(total / page_size),
        ),
    )
    return _ok(data, _request_id(x_request_id))


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    conversation = _owned_conversation(db, conversation_id, user)
    data = ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in repo.get_messages(db, conversation.id)],
    )
    return _ok(data, _request_id(x_request_id))


VN_TZ = timezone(timedelta(hours=7))  # giờ Việt Nam, không có giờ mùa hè


def _group_turns(messages: list) -> list[TranscriptTurn]:
    """Gom tin thành lượt: tin user rồi tin assistant liền sau. User cuối chưa có trả lời vẫn thành một lượt (assistant
    None); tin assistant không có user đứng trước cũng thành một lượt riêng (user None) để không mất tin."""
    turns: list[TranscriptTurn] = []
    pending: TranscriptMessage | None = None
    for m in messages:
        item = TranscriptMessage(content=m.content, created_at=m.created_at)
        if m.role == "user":
            if pending is not None:
                turns.append(TranscriptTurn(turn=len(turns) + 1, user=pending, assistant=None))
            pending = item
        else:
            turns.append(TranscriptTurn(turn=len(turns) + 1, user=pending, assistant=item))
            pending = None
    if pending is not None:
        turns.append(TranscriptTurn(turn=len(turns) + 1, user=pending, assistant=None))
    return turns


def _vn_time(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite (test) trả datetime không múi giờ; giá trị lưu luôn là UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(VN_TZ).strftime("%d/%m/%Y %H:%M")


def _transcript_text(transcript: Transcript) -> str:
    """Văn bản thuần: giữ nguyên xuống dòng bên trong nội dung tin (không thụt thêm cho các dòng sau)."""
    lines = [
        f"Hội thoại: {transcript.title or '(chưa có tiêu đề)'}",
        f"Bắt đầu: {_vn_time(transcript.created_at)}",
    ]
    for t in transcript.turns:
        lines += ["", f"Lượt {t.turn}"]
        if t.user is not None:
            lines.append(f"  Người dùng: {t.user.content}")
        lines.append(f"  Trợ lý: {t.assistant.content}" if t.assistant is not None else "  Trợ lý:")
    return "\n".join(lines) + "\n"


@router.get("/{conversation_id}/transcript")
def get_transcript(
    conversation_id: uuid.UUID,
    fmt: Literal["json", "text"] = Query(default="json", alias="format"),
    download: bool = Query(default=False),
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    """
    Cả cuộc hội thoại như một bản kịch bản (chỉ đọc), gom theo lượt hỏi-đáp, không áp giới hạn history_max_messages.
    Tối đa 500 tin đầu (`truncated` = true nếu dài hơn). `format=text` trả văn bản thuần; kèm `download=true` thì
    thêm Content-Disposition để trình duyệt tải về `kich-ban-<8 ký tự đầu của id>.txt`.
    """
    request_id = _request_id(x_request_id)
    conversation = _owned_conversation(db, conversation_id, user)
    messages, total = repo.get_messages_for_transcript(db, conversation.id)
    transcript = Transcript(
        conversation_id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        total_messages=total,
        truncated=total > len(messages),
        turns=_group_turns(messages),
    )
    if fmt == "json":
        return _ok(transcript, request_id)
    headers = {"X-Request-ID": request_id}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="kich-ban-{str(conversation.id)[:8]}.txt"'
    return Response(_transcript_text(transcript), media_type="text/plain; charset=utf-8", headers=headers)


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    conversation = _owned_conversation(db, conversation_id, user)
    owner_id, deleted_id = conversation.user_id, conversation.id  # lấy trước khi xoá
    repo.delete_conversation(db, conversation)
    history_cache.invalidate(owner_id, deleted_id)  # chỉ chạy khi Supabase đã xoá xong
    return _ok({"deleted": True}, _request_id(x_request_id))


def _load_for_chat(db: Session, conversation_id: uuid.UUID, user: TokenPayload):
    try:
        conversation = _owned_conversation(db, conversation_id, user)
        owner_id, owned_id = conversation.user_id, conversation.id
    finally:
        db.close()

    history = history_cache.get(owner_id, owned_id)
    if history is None:
        try:
            history = [
                {"role": m.role, "content": m.content}
                for m in repo.get_messages(db, owned_id, limit=get_settings().history_max_messages)
            ]
        finally:
            db.close()
        history_cache.put(owner_id, owned_id, history)
    return conversation, history


def _save_pair(db: Session, conversation: Conversation, question: str, answer: str, metadata: dict | None, asked_at: datetime):
    """Ghi cặp hỏi-đáp vào Supabase; kiểm tra hội thoại còn tồn tại và đúng chủ trước khi ghi (tránh db.merge khôi phục
    hội thoại vừa bị xóa ở tab khác trong lúc chờ LLM). CHỈ KHI ghi xong mới nối cặp đó vào cache lịch sử (ghi xuyên)."""
    owner_id, conversation_id = conversation.user_id, conversation.id
    try:
        live_conversation = repo.get_conversation_of_user(db, conversation_id, owner_id)
        if live_conversation is not None:
            user_message, assistant_message = repo.add_message_pair(db, live_conversation, question, answer, metadata, asked_at)
            history = [{"role": m.role, "content": m.content} for m in (user_message, assistant_message)]
    finally:
        db.close()
    if live_conversation is None:
        history_cache.invalidate(owner_id, conversation_id)
        raise _error(404, "CONVERSATION_NOT_FOUND", "Hội thoại không còn tồn tại.")
    history_cache.append(owner_id, conversation_id, history, get_settings().history_max_messages)
    return user_message, assistant_message


async def _load_documents(payload: SendMessageRequest, token: str | None, request_id: str) -> list[dict] | None:
    """Chữ đã trích của các tài liệu đính kèm. Quyền chủ file do document-service kiểm tra bằng token người dùng."""
    if not payload.document_ids:
        return None
    if not token:
        raise _error(401, "UNAUTHORIZED", "Bạn cần đăng nhập để tiếp tục.")
    try:
        return await document_client.get_extractions(payload.document_ids, token, request_id)
    except document_client.DocumentNotFound:
        # Không phân biệt "không tồn tại" và "của người khác" để không lộ file của người khác
        raise _error(404, "DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.")
    except document_client.DocumentNotReady as e:
        raise _error(409, "DOCUMENT_NOT_READY", str(e))
    except document_client.DocumentServiceError:
        logger.exception("document-service lỗi, request_id=%s", request_id)
        raise _error(503, "DOCUMENT_SERVICE_ERROR", "Không đọc được tài liệu lúc này, vui lòng thử lại sau.")


def _quota_metadata(result: dict) -> dict:
    """Record the quota decision without erasing the provider's tokens_used."""
    metadata = {**result["metadata"]}
    if quota.is_exempt(metadata, result.get("content", "")):
        metadata["quota_exempt"] = True
        metadata["quota_exemption_reason"] = "out_of_scope"
    return metadata


def _owner_kwargs(fn, owner_id, budget=None):
    parameters = inspect.signature(fn).parameters
    var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    kwargs = {"owner_scope": str(owner_id)} if "owner_scope" in parameters or var_kw else {}
    if budget is not None and ("budget" in parameters or var_kw):
        kwargs["budget"] = budget  # sổ cái $0.01 đã admission đi cùng mọi nhánh
    return kwargs


async def _admit(payload: SendMessageRequest, history: list[dict], documents, user_key: str, username, today: str, request_id: str):
    """Cổng admission thống nhất, chạy TRƯỚC agent và TRƯỚC khi mở SSE: từ chối thì trả JSON 413/422/429/503 (không RAG/LLM,
    không trừ quota, không lưu tin nhắn)."""
    try:
        return await run_in_threadpool(admission.admit, payload.content, history, documents=documents, subject=user_key,
                                       username=username, today=today)
    except admission.AdmissionError as exc:
        exc.request_id = request_id
        raise


# Idempotency-Key: cùng chủ + hội thoại + key + nội dung trong 5 phút thì trả lại đúng kết quả đã lưu,
# không gọi RAG/LLM và không trừ quota lần nữa. Các worker dùng chung kết quả khi cache dùng Redis.
IDEMPOTENCY_TTL, IDEMPOTENCY_MAX = 300.0, 1000
_IdempotencySlot = tuple[str, str, str]


class _IdempotencyFlight:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


_idempotency = None
_local_idempotency: cache.TTLCache | None = None
_idempotency_lock = threading.Lock()
_idempotency_flights: dict[_IdempotencySlot, _IdempotencyFlight] = {}


@contextlib.asynccontextmanager
async def _serialize_idempotency(slot: _IdempotencySlot | None) -> AsyncIterator[None]:
    if slot is None:
        yield
        return

    with _idempotency_lock:
        flight = _idempotency_flights.get(slot)
        if flight is None:
            flight = _idempotency_flights[slot] = _IdempotencyFlight()
        flight.users += 1
    try:
        async with flight.lock:
            yield
    finally:
        with _idempotency_lock:
            flight.users -= 1
            if flight.users == 0:
                del _idempotency_flights[slot]


def _idempotency_cache():
    global _idempotency
    with _idempotency_lock:
        if _idempotency is None:
            _idempotency = cache.make_cache("idempotency", IDEMPOTENCY_MAX, IDEMPOTENCY_TTL)
        return _idempotency


def _local_idempotency_cache() -> cache.TTLCache:
    global _local_idempotency
    with _idempotency_lock:
        if _local_idempotency is None:
            _local_idempotency = cache.TTLCache(IDEMPOTENCY_MAX, IDEMPOTENCY_TTL)
        return _local_idempotency


def reset_idempotency() -> None:
    """Reset cached results while active locks drain with their holders and waiters."""
    global _idempotency, _local_idempotency
    with _idempotency_lock:
        _idempotency = None
        _local_idempotency = None


def _idempotency_lookup(slot: _IdempotencySlot, payload: SendMessageRequest):
    digest = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
    shared = _idempotency_cache()
    stored = shared.get(slot)
    if stored is None:
        stored = _local_idempotency_cache().get(slot)
        if stored is not None:
            shared.set(slot, stored)
    if not isinstance(stored, dict) or not isinstance(stored.get("digest"), str):
        return None, (slot, digest)
    try:
        replay = SendMessageResponse.model_validate(stored["data"])
    except (KeyError, ValueError):
        return None, (slot, digest)
    # The response already records the attachment IDs, including in existing cache entries.
    stored_documents = (replay.assistant_message.metadata or {}).get("document_ids", [])
    requested_documents = [str(document_id) for document_id in payload.document_ids or []]
    if stored["digest"] != digest or stored_documents != requested_documents:
        raise _error(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key này đã được dùng cho một nội dung khác.")
    return replay, (slot, digest)


def _idempotency_store(token, data: SendMessageResponse) -> None:
    if token is not None:
        slot, digest = token
        value = {"digest": digest, "data": data.model_dump(mode="json")}
        _local_idempotency_cache().set(slot, value)
        _idempotency_cache().set(slot, value)


def _skip_quota(metadata, call=None):
    budget = getattr(call, "budget", None)
    if budget is not None and budget.enabled:
        return budget.attempts == 0
    summary = metadata.get("budget_summary")
    return summary is not None and summary.get("attempts") == 0


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: TokenPayload = Depends(get_current_user),
    token: str | None = Depends(raw_access_token),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    """
    1. Kiểm tra quyền, lấy lịch sử  2. Lấy chữ tài liệu đính kèm (nếu có)  3. RAG + LLM
    4. Lưu câu hỏi và câu trả lời (1 transaction).
    Tài liệu, RAG hoặc LLM lỗi thì không lưu gì, người dùng gửi lại được. Không lưu nội dung tài liệu, chỉ lưu document_ids.
    Truy vấn DB đồng bộ nên chạy trong threadpool để không chặn event loop khi chờ Gemini.
    """
    request_id = _request_id(x_request_id)
    conversation, history = await run_in_threadpool(_load_for_chat, db, conversation_id, user)
    # Gọi trực tiếp hàm endpoint (test) thì giá trị mặc định là đối tượng Header, không phải chuỗi: coi như không có khoá
    idem_key = idempotency_key if isinstance(idempotency_key, str) else None
    user_key = str(conversation.user_id)
    idem_slot = (user_key, str(conversation.id), idem_key) if idem_key else None
    async with _serialize_idempotency(idem_slot):
        idem_token = None
        if idem_slot is not None:
            replay, idem_token = await run_in_threadpool(_idempotency_lookup, idem_slot, payload)
            if replay is not None:
                return _ok(replay, request_id)
        today = quota.get_today_vn()
        user_name = getattr(user, "username", None)

        # Khóa nguyên tử theo user: chặn spam song song vượt hạn mức token ngày; giải phóng sau khi ghi nhận token.
        slot_token = await run_in_threadpool(quota.acquire_inflight_slot, user_key, username=user_name)
        try:
            await run_in_threadpool(quota.check_daily_quota, user_key, target_date=today, username=user_name)
            # Lấy tài liệu trước RAG/LLM: tài liệu lỗi thì khỏi tốn lượt gọi AI
            documents = await _load_documents(payload, token, request_id)
            decision = await _admit(payload, history, documents, user_key, user_name, today, request_id)
            asked_at = datetime.now(timezone.utc)

            try:
                result = await agent.process_message(payload.content, decision.history, request_id=request_id, documents=documents, **_owner_kwargs(agent.process_message, conversation.user_id, decision.budget))
            except rag_client.RAGError:
                logger.exception("RAG lỗi, request_id=%s", request_id)
                raise _error(503, "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại.")
            except (llm_client.LLMError, case_budget.BudgetExhaustedError):
                logger.exception("LLM lỗi, request_id=%s", request_id)
                raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

            metadata = _quota_metadata(result)
            if not _skip_quota(metadata):
                await run_in_threadpool(quota.add_daily_tokens, user_key, metadata.get("tokens_used"),
                                        target_date=today, prompt=payload.content, completion=result.get("content", ""), metadata=metadata, username=user_name)
            if payload.document_ids:
                metadata = {**metadata, "document_ids": [str(i) for i in payload.document_ids]}
            user_message, assistant_message = await run_in_threadpool(
                _save_pair, db, conversation,
                payload.content, result["content"], metadata, asked_at,
            )
            data = SendMessageResponse(
                user_message=MessageOut.model_validate(user_message),
                assistant_message=MessageOut.model_validate(assistant_message),
            )
            if idem_token is not None:
                await run_in_threadpool(_idempotency_store, idem_token, data)
            return _ok(data, request_id)
        finally:
            # Gọi đồng bộ (timeout Redis ngắn) để chắc chắn nhả khóa kể cả khi request bị hủy (CancelledError).
            quota.release_inflight_slot(user_key, slot_token)


def _sse(event: str, data) -> str:
    """Một sự kiện Server-Sent Events; JSON một dòng (ngắt dòng trong chữ đã được escape) nên không cần tách data."""
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"


class _ConversationStream(StreamingResponse):
    def __init__(self, events: AsyncGenerator[str, None], request_id: str, resources: contextlib.AsyncExitStack):
        super().__init__(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": request_id},
        )
        self._events = events
        self._resources = resources

    async def __call__(self, scope, receive, send):
        # Starlette can stop sending at a yield or before the generator starts.
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                async with self._resources:
                    await self._events.aclose()


async def _replay_stream(data: SendMessageResponse) -> AsyncGenerator[str, None]:
    yield _sse("delta", {"text": data.assistant_message.content})
    yield _sse("done", data)


async def _iter_with_heartbeat(stream_iter: AsyncIterator[str], interval: float) -> AsyncIterator[tuple[str, str | None]]:
    """Đọc từng mẩu từ `stream_iter` mà KHÔNG hủy `__anext__` khi chờ quá `interval` giây;
    đảm bảo cứ mỗi `interval` giây chưa phát sự kiện ra ngoài thì phát ("heartbeat", None) giữ kết nối SSE sống."""
    iterator = stream_iter.__aiter__()
    next_task: asyncio.Task | None = None
    last_emit = time.monotonic()
    try:
        while True:
            if next_task is None:
                next_task = asyncio.create_task(iterator.__anext__())
            wait_for = max(0.001, interval - (time.monotonic() - last_emit))
            done, _ = await asyncio.wait({next_task}, timeout=wait_for)
            if not done:
                last_emit = time.monotonic()
                yield ("heartbeat", None)
                continue
            task = next_task
            next_task = None
            try:
                text = task.result()
            except StopAsyncIteration:
                return
            yield ("chunk", text)
            if time.monotonic() - last_emit >= interval:
                last_emit = time.monotonic()
                yield ("heartbeat", None)
    finally:
        if next_task is not None and not next_task.done():
            next_task.cancel()
            with contextlib.suppress(BaseException):
                await next_task


async def _stream_events(
    db: Session,
    conversation: Conversation,
    payload: SendMessageRequest,
    request_id: str,
    asked_at: datetime,
    call: responder.LLMCall | None,
    result: dict | None,
    today: str | None = None,
    username: str | None = None,
    slot_token: str | None = None,
    idem_token=None,
) -> AsyncGenerator[str, None]:
    """delta (từng mẩu chữ) rồi done; hồ sơ phức tạp được đệm để hỗ trợ dự phòng. Chỉ lưu DB sau khi hoàn tất: lỗi giữa chừng
    hoặc client ngắt kết nối (generator bị đóng ngay tại `yield`, không chạy tới bước lưu) thì không lưu gì."""
    parts: list[str] = []
    info: dict = {}
    quota_recorded = False
    try:
        if call is not None:
            remaining_fn = getattr(call, "remaining_seconds", None)
            remaining = remaining_fn() if callable(remaining_fn) else None
            complex_call = getattr(call, "metadata_extra", {}).get("profile") == "complex"
            # Lỗi nhà cung cấp/hết giờ/hết ngân sách là LỖI (event: error, không lưu); không còn mẫu thay thế câu trả lời
            if getattr(call, "fallback_reason", None):
                raise case_budget.BudgetExhaustedError(f"Không đủ ngân sách để trả lời ({call.fallback_reason})")
            if remaining == 0:
                raise TimeoutError()
            if getattr(call, "no_evidence", False):  # không có căn cứ nào: không gọi responder, finalize_complex trả insufficient_evidence
                info["finish_reason"] = "no_evidence"
            else:
                async with asyncio.timeout(remaining):
                    stream_iter = llm_client.generate_stream(
                        call.system_prompt, call.history, call.user_content, info=info, **(getattr(call, "llm_options", None) or {}),
                    )
                    async with contextlib.aclosing(stream_iter):
                        if complex_call:
                            heartbeat = max(0.01, float(getattr(get_settings(), "sse_heartbeat_seconds", 10.0) or 10.0))
                            async for kind, item in _iter_with_heartbeat(stream_iter, heartbeat):
                                if kind == "heartbeat":
                                    yield _sse("status", {"stage": "generating", "message": "Đang tổng hợp phân tích pháp lý chi tiết…"})
                                elif item is not None:
                                    parts.append(item)
                        else:
                            async for text in stream_iter:
                                parts.append(text)
                                yield _sse("delta", {"text": text})
            if complex_call:  # cùng finalizer với POST (partial_grounded khi `length`)
                result = call.finalize_complex("".join(parts), info.get("tokens"), info.get("model"),
                                               info.get("truncated", False), info.get("finish_reason"))
            else:
                result = call.result("".join(parts), info.get("tokens"), info.get("model"),
                                     info.get("truncated", False), info.get("finish_reason"))
            if complex_call:
                yield _sse("delta", {"text": result["content"]})
        else:
            # Xong luôn (câu vô nghĩa, ngoài phạm vi, LLM_BYPASS) hoặc rà soát tài liệu: phát cả câu một lần
            yield _sse("delta", {"text": result["content"]})
        metadata = _quota_metadata(result)
        if payload.document_ids:
            metadata = {**metadata, "document_ids": [str(i) for i in payload.document_ids]}
        # Once persistence starts, finish recording the replay even if the client disconnects.
        with CancelScope(shield=True):
            user_message, assistant_message = await run_in_threadpool(
                _save_pair, db, conversation, payload.content, result["content"], metadata, asked_at,
            )
            data = SendMessageResponse(
                user_message=MessageOut.model_validate(user_message),
                assistant_message=MessageOut.model_validate(assistant_message),
            )
            if idem_token is not None:
                await run_in_threadpool(_idempotency_store, idem_token, data)
        yield _sse("done", data)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        yield _sse("error", {
            "code": detail.get("code") or f"HTTP_{exc.status_code}",
            "message": detail.get("message") or "Yêu cầu không hợp lệ.",
        })
    except (llm_client.LLMError, case_budget.BudgetExhaustedError, TimeoutError):
        logger.exception("LLM lỗi khi stream, request_id=%s", request_id)
        yield _sse("error", {"code": "AI_SERVICE_ERROR", "message": "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau."})
    except Exception:
        logger.exception("Lỗi khi stream câu trả lời, request_id=%s", request_id)
        yield _sse("error", {"code": "INTERNAL_ERROR", "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau."})

    finally:
        try:
            # Redis calls are bounded; do not await in a cancelled generator.
            auxiliary_tokens = (getattr(call, "auxiliary_tokens", 0) or 0) if call is not None else 0
            active_budget = getattr(call, "budget", None)
            budget_active = active_budget is not None and active_budget.enabled
            should_record = (active_budget.attempts > 0 if budget_active else
                             result is not None or (call is not None and (parts or info.get("tokens") or auxiliary_tokens)))
            if not quota_recorded and should_record and not _skip_quota((result or {}).get("metadata", {}), call):
                metadata = (result or {}).get("metadata", {})
                usage = metadata.get("tokens_used") or info.get("tokens")
                active_budget = getattr(call, "budget", None)
                if budget_active:
                    usage = active_budget.confirmed_tokens or None
                if result is None and auxiliary_tokens and not budget_active:
                    usage = (usage or 0) + auxiliary_tokens
                completion = result["content"] if result is not None else "".join(parts)
                quota_recorded = True
                quota.add_daily_tokens(str(conversation.user_id), usage, target_date=today,
                                       prompt=payload.content, completion=completion, metadata=metadata, username=username)
        finally:
            if slot_token:
                quota.release_inflight_slot(str(conversation.user_id), slot_token)


@router.post("/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: TokenPayload = Depends(get_current_user),
    token: str | None = Depends(raw_access_token),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    """
    Như `send_message` nhưng trả lời dạng Server-Sent Events để chữ hiện dần.
    Kiểm tra quyền, lấy lịch sử, đọc tài liệu và rag (cùng bước chấm liên quan) đều làm TRƯỚC khi mở stream, nên lỗi ở
    các bước này trả JSON lỗi bình thường (401/403/404/409/503) như endpoint cũ. Sau đó mới phát sự kiện:
    delta {"text"} (nhiều lần) -> done (SendMessageResponse đã lưu) hoặc error {"code", "message"}.
    Cặp tin nhắn chỉ được lưu sau khi stream xong đầy đủ.
    """
    request_id = _request_id(x_request_id)
    conversation, history = await run_in_threadpool(_load_for_chat, db, conversation_id, user)
    user_key = str(conversation.user_id)
    idem_key = idempotency_key if isinstance(idempotency_key, str) else None
    idem_slot = (user_key, str(conversation.id), idem_key) if idem_key else None
    resources = contextlib.AsyncExitStack()
    handoff_to_stream = False
    try:
        await resources.enter_async_context(_serialize_idempotency(idem_slot))
        idem_token = None
        if idem_slot is not None:
            replay, idem_token = await run_in_threadpool(_idempotency_lookup, idem_slot, payload)
            if replay is not None:
                return _ConversationStream(_replay_stream(replay), request_id, contextlib.AsyncExitStack())

        today = quota.get_today_vn()
        user_name = getattr(user, "username", None)
        slot_token = await run_in_threadpool(quota.acquire_inflight_slot, user_key, username=user_name)
        if slot_token:
            resources.callback(quota.release_inflight_slot, user_key, slot_token)
        await run_in_threadpool(quota.check_daily_quota, user_key, target_date=today, username=user_name)
        documents = await _load_documents(payload, token, request_id)
        # Admission TRƯỚC khi mở SSE: từ chối thì trả HTTP JSON chuẩn, chưa có header text/event-stream
        decision = await _admit(payload, history, documents, user_key, user_name, today, request_id)
        asked_at = datetime.now(timezone.utc)

        call: responder.LLMCall | None = None
        result: dict | None = None
        try:
            if documents:
                # Rà soát tài liệu không stream: chạy như cũ rồi phát cả câu trong một delta
                result = await agent.process_message(payload.content, decision.history, request_id=request_id, documents=documents, **_owner_kwargs(agent.process_message, conversation.user_id, decision.budget))
            else:
                prepared = await agent.prepare(payload.content, decision.history, request_id=request_id, **_owner_kwargs(agent.prepare, conversation.user_id, decision.budget))
                if isinstance(prepared, responder.LLMCall):
                    call = prepared
                else:
                    result = prepared
        except rag_client.RAGError:
            logger.exception("RAG lỗi, request_id=%s", request_id)
            raise _error(503, "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại.")
        except (llm_client.LLMError, case_budget.BudgetExhaustedError):
            logger.exception("LLM lỗi, request_id=%s", request_id)
            raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

        response = _ConversationStream(
            _stream_events(db, conversation, payload, request_id, asked_at, call, result, today,
                           username=user_name, slot_token=slot_token, idem_token=idem_token),
            request_id,
            resources,
        )
        handoff_to_stream = True
        return response
    finally:
        if not handoff_to_stream:
            await resources.aclose()
