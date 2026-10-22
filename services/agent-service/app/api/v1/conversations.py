import logging
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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
from app.services import agent, llm_client, rag_client

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


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    """
    1. Kiểm tra quyền, lấy lịch sử  2. RAG + Gemini  3. Lưu câu hỏi và câu trả lời (1 transaction).
    RAG hoặc Gemini lỗi thì không lưu gì, người dùng gửi lại được.
    Truy vấn DB đồng bộ nên chạy trong threadpool để không chặn event loop khi chờ Gemini.
    """
    request_id = _request_id(x_request_id)
    conversation, history = await run_in_threadpool(_load_for_chat, db, conversation_id, user)
    asked_at = datetime.now(timezone.utc)

    try:
        result = await agent.process_message(payload.content, history, request_id=request_id)
    except rag_client.RAGError:
        logger.exception("RAG lỗi, request_id=%s", request_id)
        raise _error(503, "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại.")
    except llm_client.LLMError:
        logger.exception("Gemini lỗi, request_id=%s", request_id)
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

    user_message, assistant_message = await run_in_threadpool(
        repo.add_message_pair, db, conversation,
        payload.content, result["content"], result["metadata"], asked_at,
    )
    data = SendMessageResponse(
        user_message=MessageOut.model_validate(user_message),
        assistant_message=MessageOut.model_validate(assistant_message),
    )
    return _ok(data, request_id)
