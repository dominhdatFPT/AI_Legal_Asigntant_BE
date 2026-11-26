"""Chấm độ liên quan: nhờ LLM chọn trong các đoạn luật rag tìm được những đoạn thật sự dùng trả lời câu hỏi.
rag-service luôn trả đủ top_k đoạn kể cả khi câu hỏi nằm ngoài kho luật và điểm số không phân biệt được, nên không dùng ngưỡng điểm."""
import asyncio
import json
import logging
import re

from app.core.config import get_settings
from app.services import case_budget, llm_client, prompts
from app.services.query_plan import Issue

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 100  # chỉ cần một dòng JSON


def _parse(text: str, total: int) -> list[int] | None:
    """Số thứ tự (1..total, không trùng, xếp tăng dần) trong {"relevant": [...]}; None nếu không đúng dạng.
    Cắt từ "{" đầu đến "}" cuối nên chịu được JSON bọc trong khối ```json hoặc có chữ dẫn trước/sau."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    numbers = data.get("relevant") if isinstance(data, dict) else None
    if not isinstance(numbers, list):
        return None
    # bool là con của int trong Python: loại ra, "true" không phải số thứ tự
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= total for n in numbers):
        return None
    return sorted(set(numbers))


async def grade(
    user_content: str,
    chunks: list[dict],
    request_id: str | None = None,
    info: dict | None = None,
    budget=None,
) -> list[dict] | None:
    """Trả về các đoạn liên quan theo thứ tự ban đầu (có thể rỗng = không đoạn nào dùng được).
    None = không chấm được, nơi gọi cứ dùng nguyên các đoạn: LLM lỗi hoặc quá giờ, JSON hỏng, số thứ tự ngoài phạm vi.
    `info` (nếu truyền) được điền {"model", "tokens"} của lượt chấm khi LLM trả lời được."""
    if not chunks:
        return []
    settings = get_settings()
    system_prompt = prompts.build_relevance_prompt(chunks, user_content)
    # Model phụ riêng (chỉ OpenRouter); rỗng thì không truyền, dùng OPENROUTER_MODEL như cũ
    aux_model = {"model": settings.openrouter_aux_model.strip()} if settings.openrouter_aux_model.strip() else {}
    if budget is not None and budget.enabled:
        aux_model = {"model": case_budget.resolve_effective_models(settings)[1]}
    try:
        text, tokens, model_name = await llm_client.generate(
            system_prompt, [], user_content,
            max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=settings.relevance_timeout_seconds, max_retries=0,
            reasoning_max_tokens=settings.llm_aux_reasoning_max_tokens, **aux_model,
            **({'budget': budget, 'budget_stage': 'relevance'} if budget is not None and budget.enabled else {}),
        )
    except (llm_client.LLMError, case_budget.BudgetExhaustedError) as e:
        logger.warning("agent chấm liên quan lỗi, giữ nguyên các đoạn luật request_id=%s: %s", request_id, e)
        return None
    if info is not None:
        info.update(model=model_name, tokens=tokens)
    logger.info(
        "relevance usage request_id=%s candidates=%d prompt_chars=%d model=%s tokens=%s",
        request_id, len(chunks), len(system_prompt) + len(user_content), model_name, tokens,
    )
    numbers = _parse(text, len(chunks))
    if numbers is None:
        logger.warning(
            "agent chấm liên quan trả kết quả không hợp lệ, giữ nguyên các đoạn luật request_id=%s: %r",
            request_id, text[:200],
        )
        return None
    return [chunks[n - 1] for n in numbers]


async def grade_issues(issues: list[Issue], chunks: list[dict], request_id: str | None = None,
                       info: dict | None = None, batch_size: int = 12, budget=None) -> list[dict]:
    """Grade issue batches independently, retaining per-issue retrieval on failure."""
    semaphore = asyncio.Semaphore(get_settings().case_retrieval_concurrency)

    async def one(issue: Issue, batch: list[dict]) -> tuple[list[dict], int, bool]:
        usage: dict = {}
        async with semaphore:
            try:
                relevant = await asyncio.wait_for(grade(
                    issue.question + "\n" + "\n".join(issue.evidence_needs), batch, request_id, info=usage,
                    **({'budget': budget} if budget is not None and budget.enabled else {}),
                ), get_settings().relevance_timeout_seconds)
            except (llm_client.LLMError, case_budget.BudgetExhaustedError, TimeoutError):
                relevant = None
        rows = batch if relevant is None else relevant
        return ([{**c, "matched_issue_ids": [issue.issue_id],
                  "confirmed_issue_ids": [] if relevant is None else [issue.issue_id],
                  "relevance_confirmed": relevant is not None, "grading_degraded": relevant is None}
                 for c in rows], usage.get("tokens") or 0, relevant is None)

    jobs = []
    for issue in issues:
        candidates = [c for c in chunks if issue.issue_id in c.get("matched_issue_ids", [])]
        jobs.extend(one(issue, candidates[a:a + batch_size]) for a in range(0, len(candidates), batch_size))
    results = await asyncio.gather(*jobs)
    if info is not None:
        info.update(tokens=sum(r[1] for r in results), degraded=any(r[2] for r in results))
    return [c for rows, _, _ in results for c in rows]

def _register_chunk_issues(chunk, key, ids, digest, memo):
    missing = []
    for iid in ids:
        memo_key = (key, iid, digest)
        if memo_key in memo:
            if memo[memo_key]:
                chunk['confirmed_issue_ids'].append(iid)
        else:
            missing.append(iid)
    return missing


def _assign_batch_rows(batch, valid, issue_index, memo):
    """valid: {chunk_id: {issue_idx: role}} của các hàng HỢP LỆ (None = cả batch hỏng). Hàng thiếu/hỏng chỉ làm đúng chunk đó
    thành 'chưa xác nhận'; các hàng tốt khác trong batch vẫn giữ nguyên kết luận."""
    for cid, (key, chunk, ids, digest) in enumerate(batch, 1):
        row = None if valid is None else valid.get(cid)
        if row is None:
            chunk['grading_degraded'] = True
            chunk['relevance_confirmed'] = False
            continue
        _assign_issue_rows(key, chunk, ids, digest, row, issue_index, memo)


def _assign_issue_rows(key, chunk, ids, digest, accepted, issue_index, memo):
    """accepted: {issue_idx: role} (hoặc tập số thứ tự khi không có vai trò). Memo giữ cả quyết định dương lẫn âm."""
    for iid in ids:
        index = issue_index[iid]
        ok = index in accepted
        memo[(key, iid, digest)] = ok
        if ok:
            chunk['confirmed_issue_ids'].append(iid)
            role = accepted.get(index) if isinstance(accepted, dict) else None
            if role:
                chunk.setdefault('support_roles', {})[iid] = role


def _batch_out_cap(count):
    """Hàng gọn kiểu [7,[[1,"r"],[3,"e"]]] ~16 token; tổng mọi batch còn bị trần relevance_total_output_tokens của sổ cái."""
    return 16 * count + 24


def _batch_fits(budget, settings, prompt, count):
    for provider in (p.strip() for p in settings.llm_provider.split(',') if p.strip()):
        model = {'openrouter': case_budget.resolve_effective_models(settings)[1],
                 'gemini': settings.gemini_model, 'groq': settings.groq_model}.get(provider, '')
        reasoning = settings.llm_aux_reasoning_max_tokens if provider == 'openrouter' else 0
        reservation = budget.reserve('relevance', provider, model, case_budget.estimate_chat_input_upper_bound(prompt, [], '', budget.strict_byte_upper_bound),
                                     _batch_out_cap(count), reasoning_tokens=reasoning)
        if reservation is not None:
            budget.release_unsent(reservation)
            return True
    return False


def _parse_accepted(accepted, allowed):
    """{issue_idx: role|None} hoặc None nếu hàng không hợp lệ. Mỗi phần tử là số thứ tự trần hoặc [số thứ tự, vai trò]."""
    if not isinstance(accepted, list):
        return None
    row = {}
    for item in accepted:
        if type(item) is int:
            index, role = item, None
        elif isinstance(item, list) and len(item) == 2 and type(item[0]) is int and isinstance(item[1], str):
            index, role = item[0], prompts.SUPPORT_ROLES.get(item[1].strip().lower())
            if role is None:
                return None
        else:
            return None
        if index not in allowed or index in row:
            return None
        row[index] = role
    return row


def _parse_batch_rows(entries, batch, issue_index):
    """{chunk_id: {issue_idx: role}} cho từng hàng hợp lệ. Hàng sai dạng, ngoài phạm vi, trùng lặp hoặc có vai trò lạ vắng mặt
    trong kết quả (chunk đó thành chưa xác nhận); chỉ khi cả `entries` không phải danh sách mới ném ValueError."""
    if not isinstance(entries, list):
        raise ValueError('invalid rows')
    parsed, bad = {}, set()
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2:
            continue
        cid, accepted = entry
        if type(cid) is not int or not 1 <= cid <= len(batch):
            continue
        if cid in parsed or cid in bad:  # hàng trùng cho cùng một chunk là mơ hồ: bỏ cả hai
            parsed.pop(cid, None)
            bad.add(cid)
            continue
        row = _parse_accepted(accepted, {issue_index[i] for i in batch[cid - 1][2]})
        if row is None:
            bad.add(cid)
        else:
            parsed[cid] = row
    return parsed


def _salvage_rows(text):
    """Các hàng đã hoàn chỉnh trong danh sách "rows" của JSON bị cắt/bọc chữ; hàng dở dang ở cuối bị bỏ."""
    marker = text.find('"rows"')
    start = text.find('[', marker) if marker >= 0 else -1
    if start < 0:
        raise ValueError('invalid rows')
    decoder, pos, rows = json.JSONDecoder(), start + 1, []
    while pos < len(text):
        while pos < len(text) and text[pos] in ' \n\r\t,':
            pos += 1
        if pos >= len(text) or text[pos] == ']':
            break
        try:
            row, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        rows.append(row)
    return rows


# Từ chức năng/từ pháp lý chung có trong hầu hết chunk: không đủ để chứng minh chunk liên quan đến một vấn đề cụ thể
_STOP_TERMS = {'của', 'những', 'được', 'không', 'trong', 'các', 'cho', 'với', 'khi', 'và', 'một', 'có', 'này', 'theo', 'về', 'tại',
               'quy', 'định', 'điều', 'khoản', 'luật', 'dụng', 'tắc', 'kiện', 'ngoại', 'trường', 'hợp', 'người', 'pháp', 'văn',
               'bản', 'phải', 'bên', 'hoặc', 'thì', 'như', 'mà', 'cũng', 'đã', 'sẽ', 'nào', 'đó', 'thể', 'nếu', 'điểm', 'nghị'}


def _terms(text):
    return {w for w in re.findall(r'\w{3,}', (text or '').casefold())} - _STOP_TERMS


def _lexically_unrelated(issue, chunk):
    """Lọc rẻ TRƯỚC khi tốn token grader: không chung MỘT từ nội dung nào với issue và không phải kết quả tra chính xác/dẫn chiếu."""
    sources = str(chunk.get('rank_sources') or chunk.get('retrieval_sources') or '').lower()
    if any(name in sources for name in ('exact', 'citation', 'adjacent')):
        return False
    wanted = _terms(' '.join([issue.question, *issue.evidence_needs, *getattr(issue, 'facet_queries', []), issue.retrieval_query[:600]]))
    document = chunk.get('document') or {}
    have = _terms(' '.join([chunk.get('content') or '', document.get('title') or '']))
    return bool(wanted) and not (wanted & have)


async def grade_candidates_dedup(issues, chunks, request_id=None, info=None, batch_size=12, budget=None, memo=None):
    """Chấm theo (issue, nhu cầu, chunk): mỗi cửa sổ chứng cứ duy nhất chỉ chấm một lần, quyết định độc lập theo từng issue và
    trả vai trò hỗ trợ (quy tắc/điều kiện/ngoại lệ/hậu quả). Một hàng hỏng chỉ làm đúng chunk đó thành chưa xác nhận."""
    import copy
    import hashlib
    memo = memo if memo is not None else {}
    issue_map = {i.issue_id: i for i in issues}
    issue_index = {i.issue_id: n for n, i in enumerate(issues, 1)}
    rows, aliases = {}, {}
    for original in chunks:
        c = copy.deepcopy(original)
        document = c.get('document') or {}
        doc = c.get('doc_id') or c.get('doc_number') or document.get('id') or document.get('doc_number')
        section = c.get('section_info')
        content = ' '.join((c.get('content') or '').split())
        alias = (str(doc), json.dumps(section, sort_keys=True, ensure_ascii=False), content) if doc and section and content else None
        key = str(c.get('id')) if c.get('id') is not None else ('alias:' + repr(alias) if alias else 'content:' + content)
        if alias and alias in aliases:
            key = aliases[alias]
        elif alias:
            aliases[alias] = key
        if key not in rows:
            rows[key] = c
            c['matched_issue_ids'] = []
        row = rows[key]
        row['matched_issue_ids'] = list(dict.fromkeys(row['matched_issue_ids'] + [i for i in original.get('matched_issue_ids', []) if i in issue_map]))
    pending = []
    prefiltered = 0
    window_chars = get_settings().relevance_chunk_chars or 800
    for key, chunk in rows.items():
        chunk['confirmed_issue_ids'] = []
        chunk['relevance_confirmed'] = True
        ids = chunk['matched_issue_ids']
        question = ' '.join(issue_map[i].question for i in ids)
        window = prompts.evidence_window(chunk.get('content') or '', question, window_chars)
        digest = hashlib.sha256(window.encode('utf-8')).hexdigest()
        missing = _register_chunk_issues(chunk, key, ids, digest, memo)
        kept = [i for i in missing if not _lexically_unrelated(issue_map[i], chunk)]
        prefiltered += len(missing) - len(kept)
        if kept:
            pending.append((key, chunk, kept, digest))
    total_tokens = pairs = batches = 0
    degraded = False
    size = max(1, min(12, batch_size))
    start = 0
    while start < len(pending):
        settings = get_settings()
        batch = pending[start:start + size]
        indexed_issues = {issue_index[i]: issue_map[i] for i in issue_map}
        while batch:
            items = [{'chunk': c, 'issue_ids': [issue_index[i] for i in ids]} for _, c, ids, _ in batch]
            prompt = prompts.build_dedup_relevance_prompt(items, indexed_issues, window_chars)
            if budget is None or not budget.enabled:
                break
            if _batch_fits(budget, settings, prompt, len(batch)):
                break
            batch = batch[:-1]
        if not batch:
            degraded = True
            _assign_batch_rows(pending[start:], None, issue_index, memo)
            break
        start += len(batch)
        batches += 1
        pairs += sum(len(x[2]) for x in batch)
        valid = None
        try:
            settings = get_settings()
            opts = {'model': settings.openrouter_aux_model.strip()} if settings.openrouter_aux_model.strip() else {}
            if budget is not None and budget.enabled:
                opts.update(model=case_budget.resolve_effective_models(settings)[1], budget=budget, budget_stage='relevance')
            result = await llm_client.generate(prompt, [], '', max_output_tokens=_batch_out_cap(len(batch)), timeout_seconds=settings.relevance_timeout_seconds, max_retries=0, reasoning_max_tokens=settings.llm_aux_reasoning_max_tokens, **opts)
            text, tokens, _ = result
            total_tokens += tokens or 0
            try:
                data = json.loads(text)
                entries = data.get('rows') if isinstance(data, dict) else None
            except ValueError:
                entries = _salvage_rows(text)  # JSON bị cắt (length) hoặc có chữ bọc: giữ các hàng đã hoàn chỉnh
            valid = _parse_batch_rows(entries, batch, issue_index)
            if len(valid) < len(batch):
                degraded = True
        except (llm_client.LLMError, case_budget.BudgetExhaustedError, ValueError, TypeError, TimeoutError):
            degraded = True
        _assign_batch_rows(batch, valid, issue_index, memo)
    if info is not None:
        info.update(tokens=total_tokens, degraded=degraded, unique_chunks=len(rows), pairs_evaluated=pairs, batches=batches,
                    prefiltered_pairs=prefiltered)
    return [c for c in rows.values() if c['confirmed_issue_ids'] or not c['relevance_confirmed']]
