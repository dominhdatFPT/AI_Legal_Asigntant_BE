"""Focused contracts for the incremental token-efficiency changes."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.query_plan import build_source_parts, fallback, format_plan_for_prompt
from app.services.case_budget import estimate_messages_tokens


def test_message_estimate_does_not_treat_each_utf8_byte_as_one_token():
    content = "á" * 8000  # 16,000 UTF-8 bytes
    estimated = estimate_messages_tokens("", [], content)
    assert 8889 <= estimated < 10000


def test_message_estimate_accounts_for_history_and_message_overhead():
    base = estimate_messages_tokens("", [], "")
    assert estimate_messages_tokens("", [{"role": "user", "content": "abc"}], "") > base


def test_budget_defaults_and_bounds():
    settings = Settings(_env_file=None)
    assert settings.case_token_budget_target == 0
    assert settings.case_token_budget_max == 0
    assert settings.case_complex_max_output_tokens == 3500 and settings.planner_max_output_tokens == 896
    with pytest.raises(ValidationError):
        Settings(_env_file=None, case_token_budget_target=10, case_token_budget_max=9)


def test_fallback_maps_all_numbered_parts_to_compact_checklist():
    text = "\n".join(f"Câu {i}: Vấn đề pháp lý số {i}?" for i in range(1, 31))
    parts = build_source_parts(text, [])
    plan = fallback(text, max_issues=12)
    assert [p.id for p in parts] == [p for issue in plan.issues for p in issue.part_ids]
    compact = format_plan_for_prompt(plan, parts)
    for i in range(1, 31):
        assert f'Câu {i}"' in compact
    assert text not in compact


@pytest.mark.asyncio
async def test_concurrent_reservations_respect_ceiling_and_responder_hold():
    import asyncio
    from app.services.case_budget import CaseBudget

    budget = CaseBudget(max_tokens=100, responder_reserved_output=20)

    async def reserve():
        await asyncio.sleep(0)
        return budget.reserve('planner', 'openrouter', 'deepseek/deepseek-v4.1-flash', 40, 40)

    results = await asyncio.gather(reserve(), reserve())
    assert sum(item is not None for item in results) == 1
    assert budget.in_flight_tokens == 80
    assert budget.reserve('responder', 'openrouter', 'deepseek/deepseek-v4.1-flash', 0, 20)


def test_ledger_cost_cache_release_and_unknown_usage():
    from app.services.case_budget import CaseBudget

    route = ('openrouter', 'deepseek/deepseek-v4.1-flash')
    budget = CaseBudget(max_tokens=1000, max_cost_nano_usd=1000000, responder_reserved_output=0)
    assert budget.reserve('planner', 'gemini', 'unknown', 1, 1) is None
    assert budget.reserve('planner', *route, 1, 1000) is None
    unsent = budget.reserve('planner', *route, 100, 10)
    budget.release_unsent(unsent)
    assert (budget.in_flight_tokens, budget.in_flight_cost_nano_usd) == (0, 0)
    cold = budget.reserve('planner', *route, 100, 10)
    budget.settle(cold, usage_breakdown={'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110})
    cold_cost = budget.confirmed_cost_nano_usd
    warm = budget.reserve('planner', *route, 100, 10)
    budget.settle(warm, usage_breakdown={'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110, 'cached_tokens': 100})
    assert budget.confirmed_cost_nano_usd - cold_cost < cold_cost
    assert budget.confirmed_cached_tokens == 100
    unknown = budget.reserve('planner', *route, 50, 10)
    budget.mark_dispatched(unknown)
    budget.settle(unknown)
    assert budget.unsettled_tokens == 60
    assert budget.unsettled_cost_nano_usd == unknown.cost_nano


def test_actual_usage_over_ceiling_blocks_further_calls():
    from app.services.case_budget import CaseBudget

    budget = CaseBudget(max_tokens=100, responder_reserved_output=0)
    route = ('openrouter', 'deepseek/deepseek-v4.1-flash')
    reservation = budget.reserve('planner', *route, 10, 10)
    budget.settle(reservation, total_tokens=101)
    assert budget.exceeded
    assert budget.reserve('planner', *route, 1, 1) is None


def test_compose_profile_settings(monkeypatch):
    from app.core.config import get_settings

    values = {'CASE_TOKEN_BUDGET_TARGET': '17000', 'CASE_TOKEN_BUDGET_MAX': '19000',
              'CASE_COST_BUDGET_MICRO_USD': '8000', 'CASE_RELEVANCE_DEDUP_ENABLED': 'true',
              'CASE_COMPLEX_MAX_OUTPUT_TOKENS': '2400', 'CASE_MAX_OUTPUT_TOKENS': '7500',
              'PLANNER_MAX_OUTPUT_TOKENS': '640', 'CASE_CANDIDATE_K_PER_ISSUE': '10',
              'CASE_MAX_CANDIDATES': '48', 'CASE_SELECT_MAX': '16', 'CASE_CONTEXT_TOKEN_BUDGET': '16000',
              'RELEVANCE_CHECK': 'true', 'OPENROUTER_MODEL': '', 'OPENROUTER_AUX_MODEL': ''}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.case_relevance_dedup_enabled
        assert settings.case_complex_max_output_tokens == 2400
        assert settings.planner_max_output_tokens == 640
        assert settings.case_max_output_tokens == 7500
        assert settings.case_token_budget_max == 19000
        from app.services.case_budget import resolve_effective_models
        assert resolve_effective_models(settings) == ('deepseek/deepseek-v4.1-flash',) * 2
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_answer_forwards_owner_scope(monkeypatch):
    from app.services import qa_agent

    calls = []

    async def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return {"content": "ok"}

    monkeypatch.setattr(qa_agent, "prepare", prepare)
    assert await qa_agent.answer("question", [], "request", owner_scope="owner") == {"content": "ok"}
    assert calls == [(('question', [], 'request'), {'owner_scope': 'owner'})]


@pytest.mark.asyncio
async def test_dedup_grades_shared_chunk_once_and_memoizes_both_decisions(monkeypatch):
    import copy
    from app.services import llm_client, relevance
    from app.services.query_plan import Issue

    issues = [Issue(issue_id=f'I{i}', question=f'unique question {i}', retrieval_query=f'q{i}') for i in (1, 2)]
    chunks = [{'id': 'shared', 'content': 'unique evidence marker', 'matched_issue_ids': ['I1', 'I2']}]
    original = copy.deepcopy(chunks)
    calls = []

    async def generate(prompt, *args, **kwargs):
        calls.append(prompt)
        return '{"rows": [[1, [1]]]}', 7, 'model'

    monkeypatch.setattr(llm_client, 'generate', generate)
    memo = {}
    first = await relevance.grade_candidates_dedup(issues, chunks, memo=memo)
    second = await relevance.grade_candidates_dedup(issues, chunks, memo=memo)
    assert len(calls) == 1 and calls[0].count('unique evidence marker') == 1
    assert first[0]['confirmed_issue_ids'] == ['I1']
    assert second[0]['confirmed_issue_ids'] == ['I1']
    assert chunks == original
    await relevance.grade_candidates_dedup(issues, chunks, memo={})
    assert len(calls) == 2


@pytest.mark.parametrize('payload', [
    '{"rows": [[1, [3]]]}', '{"rows": [[1, [true]]]}',
    '{"rows": [[1, [1, 1]]]}', '{"rows": []}',
    '{"rows": [[1, []], [1, []]]}',
])
@pytest.mark.asyncio
async def test_dedup_rejects_invalid_rows_without_confirming(monkeypatch, payload):
    from app.services import llm_client, relevance
    from app.services.query_plan import Issue

    async def generate(*args, **kwargs):
        return payload, 3, 'model'

    monkeypatch.setattr(llm_client, 'generate', generate)
    info = {}
    issue = Issue(issue_id='I1', question='evidence question', retrieval_query='query')
    result = await relevance.grade_candidates_dedup([issue], [{'id': 'a', 'content': 'evidence', 'matched_issue_ids': ['I1']}], info=info)
    assert info['degraded'] and result[0]['confirmed_issue_ids'] == []
    assert result[0]['relevance_confirmed'] is False


@pytest.mark.asyncio
async def test_dedup_accepts_empty_decision(monkeypatch):
    from app.services import llm_client, relevance
    from app.services.query_plan import Issue

    async def generate(*args, **kwargs):
        return '{"rows": [[1, []]]}', 1, 'model'

    monkeypatch.setattr(llm_client, 'generate', generate)
    info = {}
    issue = Issue(issue_id='I1', question='question', retrieval_query='query')
    assert await relevance.grade_candidates_dedup([issue], [{'id': 'a', 'content': 'x', 'matched_issue_ids': ['I1']}], info=info) == []
    assert not info['degraded']


def test_density_prefers_retrieval_rank_and_stable_ties():
    from app.services import evidence_selection

    rows = [{'id': 'low', 'content': 'same length', 'matched_issue_ids': ['I1'], 'score': 0.99,
             'retrieval_provenance': [{'rank': 20}]},
            {'id': 'high', 'content': 'same length', 'matched_issue_ids': ['I1'], 'score': 0.1,
             'retrieval_provenance': [{'rank': 1}]}]
    select = lambda: evidence_selection.select(rows, ['I1'], 10000, 1, use_value_density=True)
    assert select().chunks[0]['id'] == select().chunks[0]['id'] == 'high'


def test_density_excerpt_preserves_full_source_and_drops_unsplittable():
    from app.services import evidence_selection, qa_agent

    content = 'Quy định chung.\n1. Điều kiện áp dụng.\n2. Ngoại lệ áp dụng.\n3. ' + 'x' * 3000
    row = {'id': 'a', 'content': content, 'matched_issue_ids': ['I1']}
    result = evidence_selection.select([row], ['I1'], 300, 1, use_value_density=True)
    assert len(result.chunks) == 1
    assert result.chunks[0]['prompt_content'] == 'Quy định chung.\n1. Điều kiện áp dụng.\n2. Ngoại lệ áp dụng.'
    assert qa_agent._build_sources_map(result.chunks, 'S')['S1']['content'] == content
    for text, budget in [('x' * 3000, 300), (content, 20)]:
        rejected = evidence_selection.select([{**row, 'content': text}], ['I1'], budget, 1, use_value_density=True)
        assert rejected.chunks == []
        assert rejected.unpacked_issue_ids == ['I1']

def test_swap_increases_coverage_without_losing_existing_coverage():
    from app.services import evidence_selection as e

    first = {'id': 'a', 'content': 'a', 'matched_issue_ids': ['I1']}
    replacement = {'id': 'b', 'content': 'b', 'matched_issue_ids': ['I1', 'I2']}
    result = e.swap_for_coverage(e.Selection([first], ['I2'], 1), [replacement], ['I1', 'I2'], 10000, 1)
    assert result.chunks == [replacement] and result.missing_issue_ids == []


@pytest.mark.parametrize('values', [('text', 5), ('text', 5, 'model')])
def test_completion_remains_tuple_compatible(values):
    from app.services.llm_client import Completion

    usage = {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
    completion = Completion(values, 'length', usage_breakdown=usage)
    assert tuple(completion) == values
    assert completion.usage_breakdown == usage
    assert completion.finish_reason == 'length' and completion.truncated


def test_complex_prompt_and_effective_date_formatting():
    from app.services import prompts

    formatted = prompts.format_case_chunk(1, {'content': 'Quy định', 'effective_date': '2024-01-01',
                                              'validity': 'chưa xác định'})
    assert '[S1]' in formatted and 'hiệu lực: 2024-01-01' in formatted
    assert 'chưa xác định' not in formatted
    prompt = prompts.build_complex_prompt([], 'Kế hoạch', output_target=2400)
    assert '[S1]' in prompt and 'Dữ kiện/văn bản còn thiếu' in prompt and '1872 tokens' in prompt


def test_merge_rounds_only_unions_explicit_confirmations():
    from app.services import evidence_selection as e

    first = {'id': 'a', 'content': 'evidence', 'matched_issue_ids': ['I1'],
             'confirmed_issue_ids': ['I1']}
    second = {'id': 'a', 'content': 'evidence', 'matched_issue_ids': ['I2', 'I3'],
              'confirmed_issue_ids': ['I2']}
    merged = e.merge_candidates([[first], [second]], 10)[0]
    assert merged['matched_issue_ids'] == ['I1', 'I2', 'I3']
    assert merged['confirmed_issue_ids'] == ['I1', 'I2']


def test_swap_distinguishes_missing_from_unpacked():
    from app.services import evidence_selection as e

    current = {'id': 'a', 'content': 'short', 'matched_issue_ids': ['I1']}
    oversized = {'id': 'b', 'content': 'x' * 3000, 'matched_issue_ids': ['I2']}
    result = e.swap_for_coverage(e.Selection([current], [], 0), [oversized],
                                 ['I1', 'I2', 'I3'], 100, 1)
    assert result.chunks == [current]
    assert result.missing_issue_ids == ['I3']
    assert result.unpacked_issue_ids == ['I2']


@pytest.mark.asyncio
async def test_planner_cache_requires_and_isolates_owner(monkeypatch):
    import json
    from app.services import llm_client, query_plan

    settings = Settings(_env_file=None, case_relevance_dedup_enabled=True)
    monkeypatch.setattr(query_plan, 'get_settings', lambda: settings)
    monkeypatch.setattr(query_plan, '_cache', type(query_plan._cache)())
    calls = []

    async def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps({'issues': [{'s': 'I1', 'p': ['P1'], 'q': 'mua nhà',
                                       'r': 'điều kiện mua nhà'}]}), 1, 'model'

    monkeypatch.setattr(llm_client, 'generate', generate)
    text = 'Câu 1: Mua nhà?'
    await query_plan.plan(text, [])
    await query_plan.plan(text, [])
    assert len(calls) == 2
    await query_plan.plan(text, [], owner_scope='u1')
    await query_plan.plan(text, [], owner_scope='u2')
    assert len(calls) == 4
    info = {}
    await query_plan.plan(text, [], info=info, owner_scope='u1')
    assert len(calls) == 4 and info['planner_status'] == 'cache_hit'


@pytest.mark.asyncio
async def test_dedup_memo_keeps_positive_and_negative_decisions(monkeypatch):
    from app.services import llm_client, relevance
    from app.services.query_plan import Issue

    issues = [Issue(issue_id=f'I{i}', question='same evidence question', retrieval_query='query') for i in (1, 2)]
    rows = [{'id': 'a', 'content': 'evidence', 'matched_issue_ids': ['I1', 'I2']}]
    calls = []

    async def generate(*args, **kwargs):
        calls.append(1)
        return '{"rows": [[1, [1]]]}', 1, 'model'

    monkeypatch.setattr(llm_client, 'generate', generate)
    memo = {}
    first = await relevance.grade_candidates_dedup(issues, rows, memo=memo)
    second = await relevance.grade_candidates_dedup(issues, rows, memo=memo)
    assert len(calls) == 1
    assert set(memo.values()) == {True, False}
    assert first[0]['confirmed_issue_ids'] == second[0]['confirmed_issue_ids'] == ['I1']


@pytest.mark.asyncio
@pytest.mark.parametrize('content,reason', [('', 'stop'), ('partial', 'length')])
async def test_openrouter_post_settles_usage_even_on_empty_or_length(monkeypatch, content, reason):
    from types import SimpleNamespace as NS
    from app.services import llm_client
    from app.services.case_budget import CaseBudget

    model = 'deepseek/deepseek-v4.1-flash'
    settings = Settings(_env_file=None, llm_provider='openrouter', openrouter_model=model,
                        openrouter_api_key='test', openrouter_reasoning_max_tokens=0)
    monkeypatch.setattr(llm_client, 'get_settings', lambda: settings)
    response = NS(usage=NS(total_tokens=15, prompt_tokens=10, completion_tokens=5),
                  choices=[NS(message=NS(content=content), finish_reason=reason)])

    async def create(**kwargs):
        return response

    monkeypatch.setattr(llm_client, '_get_openrouter_client',
                        lambda: NS(chat=NS(completions=NS(create=create))))
    budget = CaseBudget(max_tokens=1000, responder_reserved_output=0)
    if content:
        result = await llm_client.generate('s', [], 'q', model=model, budget=budget, max_output_tokens=100)
        assert result.finish_reason == 'length'
    else:
        with pytest.raises(llm_client.LLMError):
            await llm_client.generate('s', [], 'q', model=model, budget=budget, max_output_tokens=100)
    assert budget.confirmed_tokens == 15 and budget.attempts == 1
    assert budget.summary()['admission_mode'] == 'empirical_utf8_guard'


@pytest.mark.asyncio
async def test_unverified_route_never_dispatches(monkeypatch):
    from app.services import llm_client
    from app.services.case_budget import BudgetExhaustedError, CaseBudget

    settings = Settings(_env_file=None, llm_provider='openrouter', openrouter_model='unverified',
                        openrouter_api_key='test')
    monkeypatch.setattr(llm_client, 'get_settings', lambda: settings)
    def unexpected():
        pytest.fail('unverified route called the SDK')
    monkeypatch.setattr(llm_client, '_get_openrouter_client', unexpected)
    budget = CaseBudget(max_tokens=1000, responder_reserved_output=0)
    with pytest.raises(BudgetExhaustedError):
        await llm_client.generate('s', [], 'q', max_output_tokens=100, budget=budget)
    assert budget.attempts == 0


@pytest.mark.asyncio
async def test_openrouter_stream_usage_only_frame_settles_once(monkeypatch):
    from types import SimpleNamespace as NS
    from app.services import llm_client
    from app.services.case_budget import CaseBudget

    model = 'deepseek/deepseek-v4.1-flash'
    settings = Settings(_env_file=None, llm_provider='openrouter', openrouter_model=model,
                        openrouter_api_key='test', openrouter_reasoning_max_tokens=0)
    monkeypatch.setattr(llm_client, 'get_settings', lambda: settings)

    class Stream:
        def __aiter__(self):
            async def frames():
                yield NS(choices=[NS(delta=NS(content='answer'), finish_reason=None)], usage=None)
                yield NS(choices=[], usage=NS(total_tokens=15))
            return frames()
        async def close(self):
            pass

    async def create(**kwargs):
        return Stream()

    monkeypatch.setattr(llm_client, '_get_openrouter_client',
                        lambda: NS(chat=NS(completions=NS(create=create))))
    budget = CaseBudget(max_tokens=1000, responder_reserved_output=0)
    info = {}
    chunks = [x async for x in llm_client.generate_stream('s', [], 'q', info=info, model=model,
                                                            budget=budget, max_output_tokens=100)]
    assert chunks == ['answer'] and info['tokens'] == 15
    assert 'usage_breakdown' not in info
    assert budget.confirmed_tokens == 15 and budget.attempts == 1

@pytest.mark.asyncio
async def test_dedup_reduces_batch_to_fit_budget(monkeypatch):
    import json
    from app.services import llm_client, relevance
    from app.services.case_budget import CaseBudget
    from app.services.query_plan import Issue

    settings = Settings(_env_file=None, llm_provider='openrouter',
                        openrouter_model='deepseek/deepseek-v4.1-flash',
                        llm_aux_reasoning_max_tokens=0)
    monkeypatch.setattr(relevance, 'get_settings', lambda: settings)
    issues = [Issue(issue_id='I1', question='evidence question', retrieval_query='query')]
    chunks = [{'id': str(i), 'content': ('evidence %s ' % i) * 40,
               'matched_issue_ids': ['I1']} for i in range(12)]
    batch_sizes = []
    original_fits = relevance._batch_fits

    def observe(budget, config, prompt, count):
        fits = original_fits(budget, config, prompt, count)
        if fits:
            batch_sizes.append(count)
        return fits

    async def generate(prompt, *args, **kwargs):
        count = batch_sizes[-1]
        return json.dumps({'rows': [[i, [1]] for i in range(1, count + 1)]}), 1, 'model'

    monkeypatch.setattr(relevance, '_batch_fits', observe)
    monkeypatch.setattr(llm_client, 'generate', generate)
    budget = CaseBudget(max_tokens=2500, responder_reserved_output=0)
    info = {}
    result = await relevance.grade_candidates_dedup(issues, chunks, budget=budget, info=info)
    assert len(result) == 12 and not info['degraded']
    assert len(batch_sizes) > 1 and 0 < batch_sizes[0] < 12


@pytest.mark.asyncio
async def test_budget_pipeline_legacy_grader_preserves_confirmed_chunks(monkeypatch):
    from app.services import llm_client, qa_agent, query_plan, rag_client
    from app.core.config import get_settings

    for key, value in {'ADAPTIVE_RAG_ENABLED': 'true', 'LLM_BYPASS': 'false',
                       'CASE_TOKEN_BUDGET_MAX': '19000', 'CASE_COST_BUDGET_MICRO_USD': '8000',
                       'RELEVANCE_CHECK': 'true', 'CASE_RELEVANCE_DEDUP_ENABLED': 'false',
                       'LLM_PROVIDER': 'openrouter', 'OPENROUTER_MODEL': 'deepseek/deepseek-v4.1-flash',
                       'LLM_AUX_REASONING_MAX_TOKENS': '0'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    seen = []

    async def plan(*args, **kwargs):
        return query_plan.QueryPlan(issues=[query_plan.Issue(issue_id='I1', question='question', retrieval_query='query')])

    async def search_many(queries, *args, **kwargs):
        return [[{'id': 'a', 'content': 'relevant evidence', 'matched_issue_ids': ['I1']}] for _ in queries]

    async def generate(*args, **kwargs):
        seen.append(kwargs)
        return '{"relevant": [1]}', 2, 'model'

    monkeypatch.setattr(query_plan, 'plan', plan)
    monkeypatch.setattr(rag_client, 'search_many', search_many)
    monkeypatch.setattr(llm_client, 'generate', generate)
    try:
        call = await qa_agent.prepare('Câu 1: question?\nCâu 2: another question?', [], owner_scope='u1')
        assert seen and seen[0]['budget'].enabled and seen[0]['budget_stage'] == 'relevance'
        assert call.chunks and call.chunks[0]['confirmed_issue_ids'] == ['I1']
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_compose_profile_e2e_post_and_sse_empty_model_overrides(monkeypatch):
    import json
    from types import SimpleNamespace as NS
    from app.core.config import get_settings
    from app.services import llm_client, qa_agent, query_plan, rag_client

    values = {'ADAPTIVE_RAG_ENABLED': 'true', 'LLM_BYPASS': 'false', 'CASE_TOKEN_BUDGET_TARGET': '17000',
              'CASE_TOKEN_BUDGET_MAX': '19000', 'CASE_COST_BUDGET_MICRO_USD': '8000',
              'CASE_RELEVANCE_DEDUP_ENABLED': 'true', 'CASE_COMPLEX_MAX_OUTPUT_TOKENS': '2400',
              'CASE_MAX_OUTPUT_TOKENS': '7500', 'PLANNER_MAX_OUTPUT_TOKENS': '640',
              'CASE_CANDIDATE_K_PER_ISSUE': '10', 'CASE_MAX_CANDIDATES': '48', 'CASE_SELECT_MAX': '16',
              'CASE_CONTEXT_TOKEN_BUDGET': '16000', 'RELEVANCE_CHECK': 'true', 'LLM_PROVIDER': 'openrouter',
              'OPENROUTER_API_KEY': 'test-key', 'OPENROUTER_MODEL': '', 'OPENROUTER_AUX_MODEL': ''}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(query_plan, '_cache', type(query_plan._cache)())
    calls = []
    usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    async def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get('stream'):
            async def frames():
                yield NS(choices=[NS(delta=NS(content='Đáp án'), finish_reason='stop')], usage=None)
                yield NS(choices=[], usage=usage)
            return frames()
        prompt = str(kwargs['messages'])
        if 'Tách TOÀN BỘ' in prompt:
            content = json.dumps({'issues': [{'s': 'I1', 'p': ['P1'], 'q': 'Điều kiện mua nhà?', 'r': 'điều kiện mua nhà'},
                                             {'s': 'I2', 'p': ['P2'], 'q': 'Thủ tục mua nhà?', 'r': 'thủ tục mua nhà'}]})
        elif 'rows' in prompt:
            content = '{"rows": [[1, [1, 2]]]}'
        else:
            content = 'Đáp án'
        return NS(choices=[NS(message=NS(content=content), finish_reason='stop')], usage=usage)

    async def search_many(queries, *args, **kwargs):
        return [[{'id': 'a', 'content': 'Căn cứ pháp luật về mua nhà.', 'matched_issue_ids': ['I1', 'I2']}] for _ in queries]

    monkeypatch.setattr(llm_client, '_get_openrouter_client', lambda: NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(rag_client, 'search_many', search_many)
    try:
        question = 'Câu 1: Điều kiện mua nhà là gì?\nCâu 2: Thủ tục mua nhà như thế nào?'
        await qa_agent.answer(question, [], owner_scope='u-compose')
        call = await qa_agent.prepare(question, [], owner_scope='u-compose')
        assert [x async for x in llm_client.generate_stream(call.system_prompt, call.history, call.user_content,
                                                             info={}, **call.llm_options)]
        assert len(calls) >= 4 and any('rows' in str(c['messages']) for c in calls)
        assert all(c['model'] == 'deepseek/deepseek-v4.1-flash' for c in calls)
        assert all(c['max_tokens'] <= 2400 for c in calls if 'Tách TOÀN BỘ' not in str(c['messages']) and 'rows' not in str(c['messages']))
        followup = await qa_agent.prepare('Tóm tắt ngắn lại giúp tôi', [{'role': 'assistant', 'content': 'Nội dung cũ'}])
        assert followup.metadata_extra['profile'] == 'followup_summary'
        assert followup.llm_options['max_output_tokens'] == 7500
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_post_and_sse_parity_and_quota_after_planner_cache_hit_and_zero_attempt(monkeypatch):
    import json
    import uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace as NS
    from app.api.v1 import conversations
    from app.core.config import get_settings
    from app.schemas.message import SendMessageRequest
    from app.services import llm_client, qa_agent, query_plan, rag_client

    for key, value in {'CASE_TOKEN_BUDGET_MAX': '19000', 'CASE_COST_BUDGET_MICRO_USD': '8000',
                       'CASE_RELEVANCE_DEDUP_ENABLED': 'true', 'RELEVANCE_CHECK': 'false',
                       'ADAPTIVE_RAG_ENABLED': 'true', 'LLM_BYPASS': 'false', 'LLM_PROVIDER': 'openrouter',
                       'OPENROUTER_API_KEY': 'test-key', 'OPENROUTER_MODEL': '', 'OPENROUTER_AUX_MODEL': ''}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(query_plan, '_cache', type(query_plan._cache)())
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    conversation = NS(id=conversation_id, user_id=user_id)
    quota_calls, saved = [], []
    monkeypatch.setattr(conversations, '_load_for_chat', lambda *a: (conversation, []))
    monkeypatch.setattr(conversations.quota, 'check_daily_quota', lambda *a, **k: None)
    monkeypatch.setattr(conversations.quota, 'add_daily_tokens', lambda *a, **k: quota_calls.append((a, k)))
    def save(db, conv, question, answer, metadata, asked_at):
        saved.append((answer, metadata))
        return (NS(id=uuid.uuid4(), role='user', content=question, meta=None, created_at=asked_at),
                NS(id=uuid.uuid4(), role='assistant', content=answer, meta=metadata, created_at=asked_at))
    monkeypatch.setattr(conversations, '_save_pair', save)
    usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    async def create(**kwargs):
        if kwargs.get('stream'):
            async def frames():
                yield NS(choices=[NS(delta=NS(content='Đáp án'), finish_reason='stop')], usage=None)
                yield NS(choices=[], usage=usage)
            return frames()
        content = json.dumps({'issues': [{'s': 'I1', 'p': ['P1'], 'q': 'Điều kiện mua nhà?', 'r': 'điều kiện mua nhà'},
                                         {'s': 'I2', 'p': ['P2'], 'q': 'Thủ tục mua nhà?', 'r': 'thủ tục mua nhà'}]}) if 'Tách TOÀN BỘ' in str(kwargs['messages']) else 'Đáp án'
        return NS(choices=[NS(message=NS(content=content), finish_reason='stop')], usage=usage)
    async def search_many(queries, *args, **kwargs):
        return [[{'id': 'a', 'content': 'Căn cứ mua nhà', 'matched_issue_ids': ['I1', 'I2']}] for _ in queries]
    monkeypatch.setattr(llm_client, '_get_openrouter_client', lambda: NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(rag_client, 'search_many', search_many)
    question = 'Câu 1: Điều kiện mua nhà là gì?\nCâu 2: Thủ tục mua nhà như thế nào?'
    payload, user = SendMessageRequest(content=question), NS(sub=str(user_id), username=None)
    try:
        await qa_agent.prepare(question, [], owner_scope=str(user_id))
        for budget in ('19000', '10'):
            monkeypatch.setenv('CASE_TOKEN_BUDGET_MAX', budget)
            get_settings.cache_clear()
            quota_calls.clear()
            if budget == '10':  # zero attempt: lỗi 503 ở cả POST và SSE, KHÔNG lưu mẫu và không trừ quota
                from fastapi import HTTPException
                before = len(saved)
                with pytest.raises(HTTPException) as failure:
                    await conversations.send_message(conversation_id, payload, user=user, token=None, db=object(), x_request_id=None)
                assert failure.value.status_code == 503
                response = await conversations.send_message_stream(conversation_id, payload, user=user, token=None, db=object(), x_request_id=None)
                events = [event async for event in response.body_iterator]
                assert len(events) == 1 and events[0].startswith('event: error') and 'AI_SERVICE_ERROR' in events[0]
                assert len(saved) == before and not quota_calls
                continue
            await conversations.send_message(conversation_id, payload, user=user, token=None, db=object(), x_request_id=None)
            post_answer, post_meta = saved[-1]
            assert len(quota_calls) == 1
            quota_calls.clear()
            response = await conversations.send_message_stream(conversation_id, payload, user=user, token=None, db=object(), x_request_id=None)
            events = [event async for event in response.body_iterator]
            sse_answer, sse_meta = saved[-1]
            assert any('event: done' in event for event in events)
            assert len(quota_calls) == (1 if budget == '19000' else 0)
            assert post_meta['budget_summary']['attempts'] == sse_meta['budget_summary']['attempts']
            if budget == '19000':
                assert post_meta['budget_summary']['attempts'] >= 1
                assert post_meta['tokens_used'] == sse_meta['tokens_used']
                assert post_meta['sources'] == sse_meta['sources']
            else:
                assert post_meta['budget_summary']['attempts'] == 0
                assert all(label in text for label in ('Câu 1', 'Câu 2') for text in (post_answer, sse_answer))
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_sse_cancel_before_first_frame_settles_and_records_quota_once(monkeypatch):
    import asyncio
    import uuid
    from types import SimpleNamespace as NS
    from app.api.v1 import conversations
    from app.core.config import get_settings
    from app.schemas.message import SendMessageRequest
    from app.services import llm_client, qa_agent, query_plan, rag_client

    for key, value in {'CASE_TOKEN_BUDGET_MAX': '19000', 'CASE_COST_BUDGET_MICRO_USD': '8000',
                       'CASE_RELEVANCE_DEDUP_ENABLED': 'true', 'RELEVANCE_CHECK': 'false', 'ADAPTIVE_RAG_ENABLED': 'true',
                       'LLM_BYPASS': 'false', 'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'test-key',
                       'OPENROUTER_MODEL': '', 'OPENROUTER_AUX_MODEL': ''}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(query_plan, '_cache', type(query_plan._cache)())
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    quota_calls, saved, calls = [], [], []
    dispatched, closed = asyncio.Event(), []
    monkeypatch.setattr(conversations, '_load_for_chat', lambda *a: (NS(id=conversation_id, user_id=user_id), []))
    monkeypatch.setattr(conversations.quota, 'check_daily_quota', lambda *a, **k: None)
    monkeypatch.setattr(conversations.quota, 'add_daily_tokens', lambda *a, **k: quota_calls.append((a, k)))
    monkeypatch.setattr(conversations, '_save_pair', lambda *a: saved.append(a))
    async def prepare(*args, **kwargs):
        call = await qa_agent.prepare(*args, **kwargs)
        calls.append(call)
        return call
    monkeypatch.setattr(conversations.agent, 'prepare', prepare)
    async def create(**kwargs):
        if kwargs.get('stream'):
            async def frames():
                try:
                    dispatched.set()
                    await asyncio.Event().wait()
                    yield None
                finally:
                    closed.append(True)
            return frames()
        return NS(choices=[NS(message=NS(content='{}'), finish_reason='stop')], usage=NS(total_tokens=15, prompt_tokens=10, completion_tokens=5))
    async def search_many(queries, *args, **kwargs):
        return [[{'id': 'c1', 'content': 'Điều 1', 'document': {'title': 'Luật'}}] for _ in queries]  # cần ≥1 trích đoạn để responder được gọi
    monkeypatch.setattr(llm_client, '_get_openrouter_client', lambda: NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(rag_client, 'search_many', search_many)
    try:
        payload = SendMessageRequest(content='Câu 1: Điều kiện mua nhà là gì?\nCâu 2: Thủ tục mua nhà như thế nào?')
        response = await conversations.send_message_stream(conversation_id, payload, user=NS(sub=str(user_id)), token=None, db=object(), x_request_id=None)
        task = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(dispatched.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await response.body_iterator.aclose()
        assert closed == [True] and calls[0].budget.attempts >= 1
        assert calls[0].budget.unsettled_tokens > 0
        assert not saved and len(quota_calls) == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_sse_complex_ungrounded_partial_becomes_insufficient_not_leaked_and_done_after_save(monkeypatch):
    import uuid
    from types import SimpleNamespace as NS
    from app.api.v1 import conversations
    from app.core.config import get_settings
    from app.schemas.message import SendMessageRequest
    from app.services import llm_client, query_plan, rag_client

    for key, value in {'CASE_TOKEN_BUDGET_MAX': '19000', 'CASE_COST_BUDGET_MICRO_USD': '8000',
                       'CASE_RELEVANCE_DEDUP_ENABLED': 'true', 'RELEVANCE_CHECK': 'false', 'ADAPTIVE_RAG_ENABLED': 'true',
                       'LLM_BYPASS': 'false', 'LLM_PROVIDER': 'openrouter', 'OPENROUTER_API_KEY': 'test-key',
                       'OPENROUTER_MODEL': '', 'OPENROUTER_AUX_MODEL': ''}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(query_plan, '_cache', type(query_plan._cache)())
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    quota_calls, saved, events_order = [], [], []
    monkeypatch.setattr(conversations, '_load_for_chat', lambda *a: (NS(id=conversation_id, user_id=user_id), []))
    monkeypatch.setattr(conversations.quota, 'check_daily_quota', lambda *a, **k: None)
    monkeypatch.setattr(conversations.quota, 'add_daily_tokens', lambda *a, **k: quota_calls.append((a, k)))
    def save(db, conv, question, answer, metadata, asked_at):
        events_order.append('save_assistant')
        saved.append(answer)
        return (NS(id=uuid.uuid4(), role='user', content=question, meta=None, created_at=asked_at),
                NS(id=uuid.uuid4(), role='assistant', content=answer, meta=metadata, created_at=asked_at))
    monkeypatch.setattr(conversations, '_save_pair', save)
    async def create(**kwargs):
        if kwargs.get('stream'):
            async def frames():
                yield NS(choices=[NS(delta=NS(content='PARTIAL_MUST_NOT_LEAK'), finish_reason=None)], usage=None)
                yield NS(choices=[NS(delta=NS(content=''), finish_reason='length')], usage=None)
                yield NS(choices=[], usage=NS(total_tokens=15, prompt_tokens=10, completion_tokens=5))
            return frames()
        return NS(choices=[NS(message=NS(content='{}'), finish_reason='stop')], usage=NS(total_tokens=15, prompt_tokens=10, completion_tokens=5))
    async def search_many(queries, *args, **kwargs):
        return [[] for _ in queries]
    monkeypatch.setattr(llm_client, '_get_openrouter_client', lambda: NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(rag_client, 'search_many', search_many)
    try:
        payload = SendMessageRequest(content='Câu 1: Điều kiện mua nhà là gì?\nCâu 2: Thủ tục mua nhà như thế nào?')
        response = await conversations.send_message_stream(conversation_id, payload, user=NS(sub=str(user_id)), token=None, db=object(), x_request_id=None)
        events = []
        async for event in response.body_iterator:
            events.append(event)
            if event.startswith('event: done'):
                events_order.append('emit_done')
        deltas = [event for event in events if event.startswith('event: delta')]
        # Không có căn cứ nào trong đoạn dở: trạng thái thiếu chứng cứ RÕ RÀNG, không rò phần dở và không phải mẫu tư vấn
        assert len(deltas) == 1 and 'chưa hoàn tất' in deltas[0]
        assert all('PARTIAL_MUST_NOT_LEAK' not in event for event in deltas)
        assert len(saved) == 1 and 'PARTIAL_MUST_NOT_LEAK' not in saved[0]
        assert events_order == ['save_assistant', 'emit_done'] and len(quota_calls) == 1
    finally:
        get_settings.cache_clear()