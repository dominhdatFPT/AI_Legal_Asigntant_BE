import json
import logging
import math
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, raw_access_token
from app.core.auth import TokenPayload
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
from app.services import agent, document_client, llm_client, rag_client, responder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent/conversations", tags=["conversations"])

HISTORY_LIMIT = 6  # số tin nhắn gần nhất gửi kèm cho LLM: đủ hiểu câu hỏi nối tiếp, không kéo cả cuộc hội thoại


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
    if conversation is None:
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
    items, total = repo.list_conversations(db, _user_id(user), page, page_size)
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


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    conversation = _owned_conversation(db, conversation_id, user)
    repo.delete_conversation(db, conversation)
    return _ok({"deleted": True}, _request_id(x_request_id))


def _load_for_chat(db: Session, conversation_id: uuid.UUID, user: TokenPayload):
    conversation = _owned_conversation(db, conversation_id, user)
    history = [
        {"role": m.role, "content": m.content}
        for m in repo.get_messages(db, conversation.id, limit=HISTORY_LIMIT)
    ]
    return conversation, history


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


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: TokenPayload = Depends(get_current_user),
    token: str | None = Depends(raw_access_token),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    """
    1. Kiểm tra quyền, lấy lịch sử  2. Lấy chữ tài liệu đính kèm (nếu có)  3. RAG + LLM
    4. Lưu câu hỏi và câu trả lời (1 transaction).
    Tài liệu, RAG hoặc LLM lỗi thì không lưu gì, người dùng gửi lại được. Không lưu nội dung tài liệu, chỉ lưu document_ids.
    Truy vấn DB đồng bộ nên chạy trong threadpool để không chặn event loop khi chờ Gemini.
    """
    request_id = _request_id(x_request_id)
    conversation, history = await run_in_threadpool(_load_for_chat, db, conversation_id, user)
    # Lấy tài liệu trước RAG/LLM: tài liệu lỗi thì khỏi tốn lượt gọi AI
    documents = await _load_documents(payload, token, request_id)
    asked_at = datetime.now(timezone.utc)

    try:
        result = await agent.process_message(payload.content, history, request_id=request_id, documents=documents)
    except rag_client.RAGError:
        logger.exception("RAG lỗi, request_id=%s", request_id)
        raise _error(503, "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại.")
    except llm_client.LLMError:
        logger.exception("LLM lỗi, request_id=%s", request_id)
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

    metadata = result["metadata"]
    if payload.document_ids:
        metadata = {**metadata, "document_ids": [str(i) for i in payload.document_ids]}
    user_message, assistant_message = await run_in_threadpool(
        repo.add_message_pair, db, conversation,
        payload.content, result["content"], metadata, asked_at,
    )
    data = SendMessageResponse(
        user_message=MessageOut.model_validate(user_message),
        assistant_message=MessageOut.model_validate(assistant_message),
    )
    return _ok(data, request_id)


def _sse(event: str, data) -> str:
    """Một sự kiện Server-Sent Events; JSON một dòng (ngắt dòng trong chữ đã được escape) nên không cần tách data."""
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"


async def _stream_events(
    db: Session,
    conversation: Conversation,
    payload: SendMessageRequest,
    request_id: str,
    asked_at: datetime,
    call: responder.LLMCall | None,
    result: dict | None,
) -> AsyncIterator[str]:
    """delta (từng mẩu chữ) rồi done; lỗi giữa chừng thì error. Chỉ lưu DB sau khi stream xong đầy đủ: lỗi giữa chừng
    hoặc client ngắt kết nối (generator bị đóng ngay tại `yield`, không chạy tới bước lưu) thì không lưu gì."""
    try:
        if call is not None:
            parts: list[str] = []
            info: dict = {}
            async for text in llm_client.generate_stream(call.system_prompt, call.history, call.user_content, info=info):
                parts.append(text)
                yield _sse("delta", {"text": text})
            result = call.result("".join(parts), info.get("tokens"), info.get("model"))
        else:
            # Xong luôn (câu vô nghĩa, ngoài phạm vi, LLM_BYPASS) hoặc rà soát tài liệu: phát cả câu một lần
            yield _sse("delta", {"text": result["content"]})
        metadata = result["metadata"]
        if payload.document_ids:
            metadata = {**metadata, "document_ids": [str(i) for i in payload.document_ids]}
        user_message, assistant_message = await run_in_threadpool(
            repo.add_message_pair, db, conversation,
            payload.content, result["content"], metadata, asked_at,
        )
        yield _sse("done", SendMessageResponse(
            user_message=MessageOut.model_validate(user_message),
            assistant_message=MessageOut.model_validate(assistant_message),
        ))
    except llm_client.LLMError:
        logger.exception("LLM lỗi khi stream, request_id=%s", request_id)
        yield _sse("error", {"code": "AI_SERVICE_ERROR", "message": "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau."})
    except Exception:
        logger.exception("Lỗi khi stream câu trả lời, request_id=%s", request_id)
        yield _sse("error", {"code": "INTERNAL_ERROR", "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau."})


@router.post("/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: TokenPayload = Depends(get_current_user),
    token: str | None = Depends(raw_access_token),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
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
    documents = await _load_documents(payload, token, request_id)
    asked_at = datetime.now(timezone.utc)

    call: responder.LLMCall | None = None
    result: dict | None = None
    try:
        if documents:
            # Rà soát tài liệu không stream: chạy như cũ rồi phát cả câu trong một delta
            result = await agent.process_message(payload.content, history, request_id=request_id, documents=documents)
        else:
            prepared = await agent.prepare(payload.content, history, request_id=request_id)
            if isinstance(prepared, responder.LLMCall):
                call = prepared
            else:
                result = prepared
    except rag_client.RAGError:
        logger.exception("RAG lỗi, request_id=%s", request_id)
        raise _error(503, "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại.")
    except llm_client.LLMError:
        logger.exception("LLM lỗi, request_id=%s", request_id)
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

    return StreamingResponse(
        _stream_events(db, conversation, payload, request_id, asked_at, call, result),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx (gateway) không gom lại cả câu rồi mới gửi
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": request_id},
    )
