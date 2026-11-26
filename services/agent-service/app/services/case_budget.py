from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import logging
import math
import time
import uuid

logger = logging.getLogger(__name__)

VERIFIED_PRICING = {('openrouter', 'deepseek/deepseek-v4.1-flash'): {'input_nano': 300, 'output_nano': 1200, 'cache_read_nano': 6, 'max_price': {'prompt': 0.30, 'completion': 1.20}}}

def is_verified_route(provider, model):
    return (provider, model) in VERIFIED_PRICING

def get_pricing(provider, model):
    return VERIFIED_PRICING.get((provider, model))

def resolve_effective_models(settings):
    primary = (settings.openrouter_model or "").strip() or "deepseek/deepseek-v4.1-flash"
    return primary, (settings.openrouter_aux_model or "").strip() or primary

# Cận trên token CHO NGÂN SÁCH TIỀN (khác estimate_messages_tokens bên dưới, chỉ là ước lượng kinh nghiệm):
# - mỗi token phủ ít nhất 1 byte UTF-8 (chế độ strict dùng thẳng số byte);
# - chat template của provider thêm khung cho mỗi message và cho cả hội thoại.
# Đo live OpenRouter deepseek/deepseek-v4.1-flash trên tiếng Việt: ~2,9-3,1 byte/token; 2.0 chừa dư ~45-55%
# (1.25 từng ước quá 2,4-3,1 lần và chặn oan Câu 1 với DAILY_TOKEN_LIMIT=20.000).
# shortcut: hệ số đo trên một model/tokenizer; đo lại khi đổi model.
BYTES_PER_TOKEN_BOUND = 2.0
MESSAGE_FRAMING_TOKENS = 16
CONVERSATION_FRAMING_TOKENS = 32


def estimate_text_tokens(text, strict_byte_upper_bound=False):
    n = len(str(text or '').encode('utf-8'))
    return n if strict_byte_upper_bound else math.ceil(n / BYTES_PER_TOKEN_BOUND)


def estimate_messages_input_upper_bound(messages, strict_byte_upper_bound=False):
    """Cận trên input một lần gọi chat; messages: [(role, content)] hoặc [{'role', 'content'}]."""
    total = CONVERSATION_FRAMING_TOKENS
    for m in messages:
        role, content = (m.get('role', ''), m.get('content', '')) if isinstance(m, dict) else m
        total += (estimate_text_tokens(role, strict_byte_upper_bound) + estimate_text_tokens(content, strict_byte_upper_bound)
                  + MESSAGE_FRAMING_TOKENS)
    return total


def estimate_chat_input_upper_bound(system_prompt, history, user_content, strict_byte_upper_bound=False):
    return estimate_messages_input_upper_bound(
        [('system', system_prompt), *((m.get('role', ''), m.get('content', '')) for m in history), ('user', user_content)],
        strict_byte_upper_bound)


def reservation_cost_nano(pricing, input_tokens, output_tokens):
    """Giá cold-cache (K = 0) cho mọi khoản giữ chỗ và admission: 300*I + 1200*O nano-USD."""
    return max(0, input_tokens) * pricing['input_nano'] + max(0, output_tokens) * pricing['output_nano']


def estimate_messages_tokens(system_prompt, history, user_content, strict_byte_upper_bound=False):
    """Estimate admission tokens; the empirical mode is not a verified hard cap."""
    messages = [('system', system_prompt), *((m.get('role', ''), m.get('content', '')) for m in history), ('user', user_content)]
    # Empirical admission estimate, not a tokenizer or a guaranteed upper bound.
    # Vietnamese UTF-8 commonly occupies more than one byte per token.
    def size(role, content):
        text = str(role) + str(content)
        byte_count = len(text.encode('utf-8'))
        return byte_count if strict_byte_upper_bound else max(math.ceil(len(text) / 1.8), math.ceil(byte_count / 1.8))
    return 16 + sum(size(role, content) + 16 for role, content in messages)

class BudgetExhaustedError(Exception):
    pass

@dataclass
class Reservation:
    tokens: int
    cost_nano: int
    pricing: dict | None
    dispatched: bool = False
    settled: bool = False
    stage: str = ''
    provider: str = ''
    model: str = ''
    prompt_tokens: int = 0
    output_tokens: int = 0
    kind: str = 'llm'
    request_turn_id: str | None = None

@dataclass
class CaseBudget:
    target_tokens: int = 0
    max_tokens: int = 0
    max_cost_nano_usd: int = 0
    deadline: float | None = None
    responder_reserved_output: int = 1900
    responder_reserved_input: int = 0
    confirmed_tokens: int = 0
    confirmed_input_tokens: int = 0
    confirmed_output_tokens: int = 0
    confirmed_cached_tokens: int = 0
    confirmed_cost_nano_usd: int = 0
    in_flight_tokens: int = 0
    in_flight_cost_nano_usd: int = 0
    unsettled_tokens: int = 0
    unsettled_cost_nano_usd: int = 0
    attempts: int = 0
    exceeded: bool = False
    responder_started: bool = False
    enforce_max_price: bool = True
    strict_byte_upper_bound: bool = False
    verified_tokenizer: bool = False
    # Sổ cái toàn câu hỏi: bất biến confirmed + in_flight + unsettled <= max_cost_nano_usd ở MỌI nhánh (kể cả embedding)
    branch: str = ''
    request_turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    embedding_cap_tokens: int = 1024
    embedding_price_nano: int = 150
    confirmed_embedding_tokens: int = 0
    in_flight_embedding_tokens: int = 0
    unsettled_embedding_tokens: int = 0
    embedding_attempts: int = 0
    planner_output_cap: int = 0
    responder_output_cap: int = 0
    responder_input_cap: int = 0
    responder_min_output: int = 0  # sàn output chất lượng đã giữ cho responder (0 = dùng sàn 256 cũ)
    stage_caps: dict = field(default_factory=dict)  # stage -> {'input': n, 'output': n}, cộng dồn mọi lần gọi của stage
    stage_used: dict = field(default_factory=dict)
    ledger: list = field(default_factory=list)

    @property
    def admission_mode(self):
        return "strict_byte_upper_bound" if self.strict_byte_upper_bound else "empirical_utf8_guard"

    def summary(self):
        return {"attempts": self.attempts, "confirmed_tokens": self.confirmed_tokens,
                "confirmed_cost_nano_usd": self.confirmed_cost_nano_usd,
                "unsettled_tokens": self.unsettled_tokens, "exceeded": self.exceeded,
                "admission_mode": self.admission_mode, "verified_tokenizer": self.verified_tokenizer,
                "branch": self.branch, "request_turn_id": self.request_turn_id, "budget_nano_usd": self.max_cost_nano_usd,
                "reserved_cost_nano_usd": self.in_flight_cost_nano_usd, "unsettled_cost_nano_usd": self.unsettled_cost_nano_usd,
                "embedding_tokens": self.confirmed_embedding_tokens, "ledger": list(self.ledger)}

    @property
    def enabled(self):
        return self.max_tokens > 0 or self.max_cost_nano_usd > 0

    @property
    def ledger_total_nano(self):
        return self.confirmed_cost_nano_usd + self.in_flight_cost_nano_usd + self.unsettled_cost_nano_usd

    @property
    def reserved_for_responder_tokens(self):
        return 0 if self.responder_started else self.responder_reserved_input + self.responder_reserved_output

    @property
    def reserved_for_responder_cost_nano(self):
        p = VERIFIED_PRICING[('openrouter', 'deepseek/deepseek-v4.1-flash')]
        return 0 if self.responder_started else self.responder_reserved_input * p['input_nano'] + self.responder_reserved_output * p['output_nano']

    def clamp_output(self, stage, requested):
        """Trần output đã được admission cấp cho stage; không có trần thì giữ nguyên yêu cầu."""
        cap = self.responder_output_cap if stage == 'responder' else self.planner_output_cap if stage == 'planner' else 0
        return min(requested, cap) if self.enabled and cap > 0 else requested

    def reserve_responder_floor(self, input_tokens, output_tokens):
        """Giữ NGAY từ đầu cả input lẫn output tối thiểu của responder, để planner/grader/embedding không làm đói nó.
        False (không giữ gì) nếu sàn đó không còn vừa trần tiền."""
        p = VERIFIED_PRICING[('openrouter', 'deepseek/deepseek-v4.1-flash')]
        if self.max_cost_nano_usd > 0 and self.ledger_total_nano + reservation_cost_nano(p, input_tokens, output_tokens) > self.max_cost_nano_usd:
            return False
        self.responder_reserved_input, self.responder_reserved_output = max(0, input_tokens), max(0, output_tokens)
        return True

    def _at_cap(self):
        return bool((self.max_tokens > 0 and self.confirmed_tokens + self.in_flight_tokens + self.unsettled_tokens >= self.max_tokens)
                    or (self.max_cost_nano_usd > 0 and self.ledger_total_nano >= self.max_cost_nano_usd))

    def _log(self, res, inp, cached, out, reasoning, finish_reason, confirmed, unsettled):
        self.ledger.append({
            "stage": res.stage, "input_tokens": inp, "cached_tokens": cached, "output_tokens": out, "reasoning_tokens": reasoning,
            "max_output_cap_sent": res.output_tokens, "provider": res.provider or ("rag-service" if res.kind == "embedding" else ""),
            "model": res.model, "finish_reason": finish_reason, "confirmed_cost_nano_usd": confirmed,
            "unsettled_cost_nano_usd": unsettled, "request_turn_id": res.request_turn_id})

    def reserve(self, stage, provider, model, prompt_tokens_upper, max_output_tokens, reasoning_tokens=0):
        p = get_pricing(provider, model)
        if self.enabled and (self.exceeded or not p or (self.deadline is not None and time.monotonic() >= self.deadline)):
            return None
        prompt, output = max(0, prompt_tokens_upper), max(0, max_output_tokens) + max(0, reasoning_tokens)
        tokens = prompt + output
        cost = prompt * p['input_nano'] + output * p['output_nano'] if p else 0
        held_tokens = self.reserved_for_responder_tokens if stage != 'responder' else 0
        held_cost = self.reserved_for_responder_cost_nano if stage != 'responder' else 0
        total = self.confirmed_tokens + self.in_flight_tokens + self.unsettled_tokens + tokens + held_tokens
        total_cost = self.ledger_total_nano + cost + held_cost
        if self.enabled and ((self.max_tokens > 0 and total > self.max_tokens) or (self.max_cost_nano_usd > 0 and total_cost > self.max_cost_nano_usd)):
            return None
        cap, used = self.stage_caps.get(stage), self.stage_used.setdefault(stage, [0, 0])
        if self.enabled and cap and ((cap.get('input') is not None and used[0] + prompt > cap['input'])
                                     or (cap.get('output') is not None and used[1] + output > cap['output'])):
            return None
        if stage == 'responder':
            self.responder_started = True
        used[0] += prompt
        used[1] += output
        self.in_flight_tokens += tokens
        self.in_flight_cost_nano_usd += cost
        return Reservation(tokens, cost, p, stage=stage, provider=provider, model=model, prompt_tokens=prompt,
                           output_tokens=output, request_turn_id=self.request_turn_id)

    def reserve_embedding(self, tokens, stage='embedding', request_turn_id=None):
        """Giữ tokens * giá embedding TRƯỚC khi gọi rag-service. Được cấp ÍT HƠN nếu vượt trần embedding hoặc phần tiền còn
        lại; `res.tokens` là số token thật sự được cấp (= embedding_token_budget gửi sang rag). None = không còn gì để cấp."""
        granted = min(max(0, int(tokens)), self.embedding_headroom())
        if granted <= 0:
            return None
        cost = granted * self.embedding_price_nano
        self.in_flight_embedding_tokens += granted
        self.in_flight_cost_nano_usd += cost
        return Reservation(granted, cost, None, stage=stage, kind='embedding',
                           request_turn_id=request_turn_id or self.request_turn_id)

    def embedding_headroom(self):
        if self.enabled and (self.exceeded or (self.deadline is not None and time.monotonic() >= self.deadline)):
            return 0
        tokens = self.embedding_cap_tokens - self.confirmed_embedding_tokens - self.in_flight_embedding_tokens - self.unsettled_embedding_tokens
        if self.max_cost_nano_usd > 0 and self.embedding_price_nano > 0:
            room = self.max_cost_nano_usd - self.ledger_total_nano - self.reserved_for_responder_cost_nano
            tokens = min(tokens, room // self.embedding_price_nano)
        return max(0, tokens)

    def settle_embedding(self, res, usage=None, *, sent=True):
        """Quyết toán theo `data.embedding_usage` của rag-service. Thiếu usage / timeout SAU KHI đã gửi thì giữ nguyên khoản giữ
        chỗ trong unsettled (trần không bao giờ bị vượt); chưa gửi thì nhả hết."""
        if res.settled or res.kind != "embedding":
            return
        res.settled = True
        self.in_flight_embedding_tokens -= res.tokens
        self.in_flight_cost_nano_usd -= res.cost_nano
        if not sent:
            return
        self.embedding_attempts += 1
        usage = usage if isinstance(usage, dict) else {}
        tokens = usage.get("prompt_tokens")
        cost = usage.get("cost_nano_usd")
        cached = usage.get("cached_tokens", 0)
        uncertain = (
            usage.get("usage_uncertain") is True
            or (usage.get("skipped_reason") == "embedding_failed" and "usage_uncertain" not in usage)
            or (tokens is None and cost is None)
            or any(type(value) is not int or value < 0 for value in (tokens, cost, cached) if value is not None)
        )
        if not uncertain:
            tokens = tokens or 0
            cost = tokens * self.embedding_price_nano if cost is None else cost
            if tokens > res.tokens or cost > res.cost_nano:
                logger.error("Embedding usage over reserve turn=%s tokens=%d cost_nano=%d reserved_tokens=%d reserved_cost_nano=%d",
                             res.request_turn_id, tokens, cost, res.tokens, res.cost_nano)
                uncertain = self.exceeded = True
        if uncertain:
            self.unsettled_embedding_tokens += res.tokens
            self.unsettled_cost_nano_usd += res.cost_nano
            self._log(res, 0, 0, 0, 0, None, 0, res.cost_nano)
            self.exceeded |= self._at_cap()
            return
        self.confirmed_embedding_tokens += tokens
        self.confirmed_cost_nano_usd += cost
        self._log(res, tokens, cached or 0, 0, 0, usage.get('skipped_reason'), cost, 0)
        self.exceeded |= self._at_cap()

    def mark_dispatched(self, res):
        if not res.dispatched and not res.settled:
            res.dispatched = True
            self.attempts += 1

    def release_unsent(self, res):
        if not res.dispatched and not res.settled:
            if res.kind == "embedding":
                self.settle_embedding(res, sent=False)
                return
            used = self.stage_used.get(res.stage)
            if used:
                used[0] -= res.prompt_tokens
                used[1] -= res.output_tokens
            self.settle(res, sent=False)

    def settle(self, res, *, usage_breakdown=None, total_tokens=None, sent=True, finish_reason=None):
        if res.settled:
            return
        res.settled = True
        self.in_flight_tokens -= res.tokens
        self.in_flight_cost_nano_usd -= res.cost_nano
        if not sent:
            return
        u = usage_breakdown or {}
        total = u.get('total_tokens') if u.get('total_tokens') is not None else total_tokens
        if total is None and u.get('prompt_tokens') is not None and u.get('completion_tokens') is not None:
            total = u['prompt_tokens'] + u['completion_tokens']
        if total is None:
            self.unsettled_tokens += res.tokens
            self.unsettled_cost_nano_usd += res.cost_nano
            self._log(res, 0, 0, 0, 0, finish_reason, 0, res.cost_nano)
            self.exceeded |= self._at_cap()
            return
        self.confirmed_tokens += max(0, int(total))
        inp = max(0, int(u.get('prompt_tokens') or 0))
        out = max(0, int(u.get('completion_tokens') or 0))
        cached = min(inp, max(0, int(u.get('cached_tokens') or 0)))
        self.confirmed_input_tokens += inp
        self.confirmed_output_tokens += out
        self.confirmed_cached_tokens += cached
        if u.get('cost_usd') is not None:
            cost = int((Decimal(str(u['cost_usd'])) * 1000000000).to_integral_value(rounding=ROUND_CEILING))
        elif res.pricing and u.get('prompt_tokens') is not None and u.get('completion_tokens') is not None:
            p = res.pricing
            cost = (inp - cached) * p['input_nano'] + cached * p['cache_read_nano'] + out * p['output_nano']
        else:
            cost = max(res.cost_nano, int(total) * (res.pricing or {}).get('output_nano', 0))
        self.confirmed_cost_nano_usd += max(0, cost)
        self._log(res, inp, cached, out, max(0, int(u.get('reasoning_tokens') or 0)), finish_reason, max(0, cost), 0)
        self.exceeded |= self._at_cap()

    def available_responder_output_tokens(self, provider, model, prompt_tokens_upper, desired_cap, min_output=256, reasoning_tokens=0):
        p = get_pricing(provider, model)
        if self.enabled and (not p or self.exceeded or (self.deadline is not None and time.monotonic() >= self.deadline)):
            return 0
        cap = max(0, desired_cap)
        if self.responder_output_cap > 0:
            cap = min(cap, self.responder_output_cap)
        if self.max_tokens > 0:
            cap = min(cap, self.max_tokens - self.confirmed_tokens - self.in_flight_tokens - self.unsettled_tokens - prompt_tokens_upper - reasoning_tokens)
        if self.max_cost_nano_usd > 0 and p:
            remaining = self.max_cost_nano_usd - self.ledger_total_nano - prompt_tokens_upper * p['input_nano']
            cap = min(cap, remaining // p['output_nano'] - reasoning_tokens)
        return cap if cap >= min_output else 0
