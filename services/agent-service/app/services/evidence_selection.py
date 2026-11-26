"""Deduplicate and pack evidence with issue coverage before additional depth."""
import copy
from collections import Counter
from dataclasses import dataclass, field
from itertools import zip_longest

from app.services import case_budget, prompts

# Khía cạnh pháp lý đã xác nhận mà bộ chọn chứng cứ thưởng khi một đoạn phủ thêm (mỗi khía cạnh tính riêng theo từng vấn đề)
FACET_TERMS = ("lỗi", "lãi", "bồi thường", "phạt cọc", "thời hiệu", "hiệu lực", "chuyển mục đích",
               "trở ngại khách quan", "bất khả kháng", "thẩm quyền", "ngoại lệ", "điều kiện")
FACET_WEIGHT = 1.0
CLAUSE_WEIGHT = 0.5  # một khoản/điều khác trong cùng văn bản vẫn có giá trị riêng (K.4, K.5 Đ.6 NQ 03 cạnh K.3)


def token_cost(text: str) -> int:
    """Token ƯỚC LƯỢNG THEO CẬN TRÊN của sổ cái (case_budget.estimate_text_tokens), không phải số byte UTF-8.
    shortcut: chưa có tokenizer chính thức của endpoint; byte được ghi riêng ở `evidence_bytes`."""
    return case_budget.estimate_text_tokens(text)


def byte_cost(text: str) -> int:
    return len(text.encode("utf-8"))


def merge_candidates(groups: list[list[dict]], limit: int) -> list[dict]:
    # Round-robin admission prevents the first issue consuming the candidate cap.
    merged: dict[str, dict] = {}
    for group in zip_longest(*groups):
        for chunk in group:
            if chunk is None:
                continue
            key = str(chunk.get("id") or " ".join(chunk.get("content", "").split()))
            if key not in merged:
                if len(merged) >= limit:
                    continue
                merged[key] = copy.deepcopy(chunk)
            row = merged[key]
            row["matched_issue_ids"] = sorted(set(row.get("matched_issue_ids", [])) | set(chunk.get("matched_issue_ids", [])))
            if "confirmed_issue_ids" in row or "confirmed_issue_ids" in chunk:
                row["confirmed_issue_ids"] = sorted(set(row.get("confirmed_issue_ids") or []) | set(chunk.get("confirmed_issue_ids") or []))
                if row["confirmed_issue_ids"]:
                    row["relevance_confirmed"] = True
            row["retrieval_provenance"] = row.get("retrieval_provenance", []) + [
                p for p in chunk.get("retrieval_provenance", []) if p not in row.get("retrieval_provenance", [])
            ]
    return list(merged.values())


@dataclass
class Selection:
    chunks: list[dict]
    missing_issue_ids: list[str]
    tokens: int
    unpacked_issue_ids: list[str] = field(default_factory=list)
    bytes: int = 0


def select(chunks: list[dict], issue_ids: list[str], token_budget: int, max_chunks: int, issues=None,
           use_value_density: bool = False, confirmed_only: bool = False,
           max_prompt_tokens: int | None = None, prompt_overhead_tokens: int = 0) -> Selection:
    if use_value_density:
        return _select_density(chunks, issue_ids, token_budget, max_chunks, issues, confirmed_only,
                               max_prompt_tokens, prompt_overhead_tokens)
    # Same text under different IDs is still the same evidence; merge coverage.
    dedup: dict[str, dict] = {}
    for c in chunks:
        document = c.get("document") or {}
        document_key = document.get("id") or document.get("doc_number") or ""
        text_key = " ".join(c.get("content", "").split()) or str(c.get("id"))
        key = f"{document_key}:{text_key}"
        if key in dedup:
            dedup[key]["matched_issue_ids"] = sorted(set(dedup[key].get("matched_issue_ids", [])) | set(c.get("matched_issue_ids", [])))
        else:
            dedup[key] = copy.deepcopy(c)
    remaining = list(dedup.values())
    selected: list[dict] = []
    covered: set[str] = set()
    used = 0
    counts: Counter = Counter()

    def take(candidate: dict) -> bool:
        nonlocal used
        remaining.remove(candidate)
        cost = token_cost(prompts.format_case_chunk(len(selected) + 1, candidate) + "\n\n")
        if used + cost > token_budget:
            return False
        selected.append(candidate)
        covered.update(candidate.get("matched_issue_ids", []))
        counts.update(set(candidate.get("matched_issue_ids", [])) & set(issue_ids))
        used += cost
        return True

    for issue_id in issue_ids:
        if issue_id in covered:
            continue
        matches = sorted((c for c in remaining if issue_id in c.get("matched_issue_ids", [])),
                         key=lambda c: c.get("score") or 0, reverse=True)
        for candidate in matches:
            if len(selected) >= max_chunks or take(candidate):
                break
    while remaining and len(selected) < max_chunks:
        def priority(c: dict) -> tuple:
            ids = set(c.get("matched_issue_ids", [])) & set(issue_ids)
            content = (c.get("content") or "").lower()
            need = any(term in content for term in ("ngoại lệ", "chuyển tiếp", "tố tụng", "thời hiệu", "thẩm quyền"))
            return (-min((counts[i] for i in ids), default=10**6), need, c.get("score") or 0)
        take(max(remaining, key=priority))
    return Selection(selected, [i for i in issue_ids if i not in covered], used)


def _index_entry(indexed, entry):
    for iid in entry['ids']:
        indexed[iid].append(entry)


def _take_first(ordered, taken, take):
    for entry in ordered:
        if entry['index'] not in taken and take(entry):
            break


def _best_density(candidates, taken, seen_facets, seen_clauses, evidence, selected_count):
    best, best_value = None, 0
    for entry in candidates:
        if entry['index'] in taken:
            continue
        facets, clause = evidence(entry)
        value = FACET_WEIGHT * len(facets - seen_facets) + (CLAUSE_WEIGHT if clause and clause not in seen_clauses else 0)
        cost = token_cost(prompts.format_case_chunk(selected_count + 1, entry['chunk']) + '\n\n')
        density = value / max(1, cost)
        if density > best_value or (density == best_value and density > 0 and best is not None and entry['tie'] < best['tie']):
            best, best_value = entry, density
    return best


def _chunk_text(chunk) -> str:
    return (chunk.get('prompt_content') or chunk.get('content') or '').casefold()


def wanted_facets(issue) -> list[str]:
    """Khía cạnh mà planner yêu cầu cho issue (nhu cầu chứng cứ + truy vấn riêng từng khía cạnh)."""
    text = ' '.join([*(getattr(issue, 'evidence_needs', None) or []), *(getattr(issue, 'facet_queries', None) or [])]).casefold()
    return [term for term in FACET_TERMS if term in text]


def _clause_key(chunk):
    document = chunk.get('document') or {}
    section = chunk.get('section_info') or {}
    doc = document.get('id') or document.get('doc_number') or chunk.get('doc_id')
    key = (doc, section.get('dieu'), section.get('khoan'), section.get('diem'))
    return key if any(key[1:]) else (doc, chunk.get('id'))


def missing_facets(chunks, issues, confirmed_only=False) -> dict[str, list[str]]:
    """{issue_id: [khía cạnh được yêu cầu mà chưa đoạn nào đã chọn (và đã xác nhận cho issue đó) chứa]}."""
    key = 'confirmed_issue_ids' if confirmed_only else 'matched_issue_ids'
    out = {}
    for issue in issues or []:
        texts = [_chunk_text(c) for c in chunks if issue.issue_id in c.get(key, [])]
        gaps = [term for term in wanted_facets(issue) if not any(term in text for text in texts)]
        if gaps:
            out[issue.issue_id] = gaps
    return out


def pack_stats(chunks, issue_ids, confirmed_only=False) -> dict:
    """Đo lại SAU lần đóng gói cuối đúng như phần chứng cứ thực gửi cho responder."""
    key = 'confirmed_issue_ids' if confirmed_only else 'matched_issue_ids'
    covered = {i for c in chunks for i in c.get(key, [])}
    text = ''.join(prompts.format_case_chunk(n, c) + '\n\n' for n, c in enumerate(chunks, 1))
    return {'missing_issue_ids': [i for i in issue_ids if i not in covered], 'evidence_tokens': token_cost(text),
            'evidence_bytes': byte_cost(text)}


def _shrink_oversized_chunk(chunk, issue_ids, issue_map):
    """Keep a contiguous prefix of whole legal paragraphs, or reject unsafe text."""
    import re
    content = chunk.get('content') or ''
    parts = [part for part in re.split(r'\n+', content) if part.strip()]
    if len(parts) < 2:
        return None
    conditions = ('trừ trường hợp', 'ngoại lệ', 'không áp dụng', 'chuyển tiếp',
                  'điều kiện', 'trừ khi', 'nếu', 'tuy nhiên', 'quy định tại', 'theo khoản')
    words = set()
    for iid in issue_ids:
        issue = issue_map.get(iid)
        text = ' '.join([getattr(issue, 'question', '') or '', *(getattr(issue, 'evidence_needs', []) or [])])
        words.update(word.lower() for word in re.findall(r'[^\W\d_]{4,}', text))
    relevant = [i for i, part in enumerate(parts) if any(term in part.lower() for term in conditions)
                or any(word in part.lower() for word in words)]
    if not relevant:
        return None
    end = max(relevant)
    if end == len(parts) - 1:
        return None
    return '\n'.join(parts[:end + 1])


def _select_density(chunks, issue_ids, token_budget, max_chunks, issues, confirmed_only, max_prompt_tokens, prompt_overhead_tokens):
    wanted_ids = set(issue_ids)
    issue_map = {getattr(i, 'issue_id', None): i for i in (issues or [])}
    indexed = {i: [] for i in issue_ids}
    candidates = []
    for index, original in enumerate(chunks):
        c = copy.deepcopy(original)
        ids = set(c.get('confirmed_issue_ids', []) if confirmed_only else c.get('matched_issue_ids', [])) & wanted_ids
        if not ids:
            continue
        provenance = c.get('retrieval_provenance') or []
        rank = min((p.get('rank', 10**9) for p in provenance if isinstance(p, dict) and isinstance(p.get('rank'), (int, float))), default=10**9)
        sources = str(c.get('retrieval_sources') or '').lower()
        tier = next((n for n, name in enumerate(('exact', 'lexical', 'vector')) if name in sources), 3)
        tie = (rank, tier, -(c.get('score') or 0), index, str(c.get('id') or ''))
        entry = {'chunk': c, 'ids': ids, 'tie': tie, 'index': index}
        candidates.append(entry)
        _index_entry(indexed, entry)
    selected, covered, used, used_bytes, taken = [], set(), 0, 0, set()
    max_tokens = min(token_budget, max_prompt_tokens - prompt_overhead_tokens) if max_prompt_tokens is not None else token_budget
    max_tokens = max(0, max_tokens)

    def take(entry):
        nonlocal used, used_bytes
        c = entry['chunk']
        text = prompts.format_case_chunk(len(selected) + 1, c) + '\n\n'
        cost = token_cost(text)
        if used + cost > max_tokens:
            excerpt = _shrink_oversized_chunk(c, entry['ids'], issue_map)
            if excerpt is None:
                return False
            c['prompt_content'] = excerpt
            text = prompts.format_case_chunk(len(selected) + 1, c) + '\n\n'
            cost = token_cost(text)
            if used + cost > max_tokens:
                c.pop('prompt_content', None)
                return False
        used += cost
        used_bytes += byte_cost(text)
        selected.append(c)
        taken.add(entry['index'])
        covered.update(entry['ids'])
        return True

    for iid in sorted(issue_ids, key=lambda i: (len(indexed[i]), issue_ids.index(i))):
        if iid in covered or len(selected) >= max_chunks:
            continue
        ordered = sorted(indexed[iid], key=lambda e: (iid not in e['chunk'].get('confirmed_issue_ids', []), -len(e['ids'] - covered), e['tie']))
        _take_first(ordered, taken, take)
    seen_facets, seen_clauses = set(), set()
    wanted = {iid: set(wanted_facets(issue_map.get(iid))) for iid in issue_ids}

    def evidence(entry):
        c = entry['chunk']
        text = _chunk_text(c)
        facets = {(iid, term) for iid in entry['ids'] for term in wanted.get(iid, ()) if term in text}
        return facets, _clause_key(c)

    for entry in candidates:
        if entry['index'] in taken:
            facets, clause = evidence(entry)
            seen_facets.update(facets)
            seen_clauses.add(clause)
    while len(selected) < max_chunks:
        best = _best_density(candidates, taken, seen_facets, seen_clauses, evidence, len(selected))
        if best is None:
            break
        taken.add(best['index'])
        if take(best):
            facets, clause = evidence(best)
            seen_facets.update(facets)
            seen_clauses.add(clause)
    missing = [i for i in issue_ids if i not in covered and not indexed[i]]
    unpacked = [i for i in issue_ids if i not in covered and indexed[i]]
    return Selection(selected, missing, used, unpacked, used_bytes)


def _try_swap(current, candidate, before, issue_ids, limit, max_chunks, coverage):
    wanted = set(issue_ids)
    for pos in range(len(current)):
        trial = current[:pos] + current[pos + 1:] + [candidate]
        after = coverage(trial)
        cost = sum(token_cost(prompts.format_case_chunk(i, c) + '\n\n') for i, c in enumerate(trial, 1))
        if before <= after and len(after & wanted) > len(before & wanted) and len(trial) <= max_chunks and cost <= limit:
            size = sum(byte_cost(prompts.format_case_chunk(i, c) + '\n\n') for i, c in enumerate(trial, 1))
            return Selection(trial, [i for i in issue_ids if i not in after], cost, [], size)
    return None


def swap_for_coverage(selection, remaining_candidates, issue_ids, token_budget, max_chunks, issues=None, confirmed_only=False, max_prompt_tokens=None, prompt_overhead_tokens=0):
    current = selection.chunks
    pool = list(remaining_candidates) + list(current)
    def coverage(chunks):
        return set().union(*(set(c.get('confirmed_issue_ids', []) if confirmed_only else c.get('matched_issue_ids', [])) for c in chunks)) if chunks else set()
    before = coverage(current)
    for candidate in remaining_candidates:
        added = coverage([candidate]) - before
        if not added:
            continue
        limit = min(token_budget, max_prompt_tokens - prompt_overhead_tokens) if max_prompt_tokens is not None else token_budget
        swapped = _try_swap(current, candidate, before, issue_ids, limit, max_chunks, coverage)
        if swapped is not None:
            selection = swapped
            break
    covered = coverage(selection.chunks)
    available = coverage(pool + selection.chunks)
    selection.missing_issue_ids = [i for i in issue_ids if i not in covered and i not in available]
    selection.unpacked_issue_ids = [i for i in issue_ids if i not in covered and i in available]
    return selection
