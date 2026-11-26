import logging

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_current_user
from app.api.v1.conversations import _error, _ok, _request_id
from app.core.auth import TokenPayload
from app.schemas.form_check import FormCheckRequest
from app.services import case_budget, form_check, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["form-check"])


@router.post("/form-check")
async def check_form(
    payload: FormCheckRequest,
    request: Request,
    user: TokenPayload = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    request_id = _request_id(x_request_id)
    request.state.request_id = request_id
    try:
        data = await form_check.check(payload, user.sub, user.username, request_id)
    except form_check.FormCheckLimitExceeded:
        raise _error(429, "FORM_CHECK_LIMIT_EXCEEDED", "Bạn đã dùng hết lượt kiểm tra AI hôm nay.")
    except form_check.FormCheckBudgetExceeded:
        raise _error(422, "QUESTION_EXCEEDS_BUDGET", "Nội dung biểu mẫu quá dài cho một lượt kiểm tra. Vui lòng rút gọn bớt các ô văn bản dài. Lượt này chưa trừ hạn mức.")
    except (llm_client.LLMError, case_budget.BudgetExhaustedError):
        logger.warning("form-check LLM lỗi, request_id=%s", request_id)  # không log nội dung
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")
    except ValueError:  # gồm JSONDecodeError và ValidationError
        logger.warning("form-check AI trả sai dạng, request_id=%s", request_id)
        raise _error(503, "AI_INVALID_RESPONSE", "AI trả kết quả không hợp lệ, vui lòng thử lại.")
    return _ok(data, request_id)
