# Adaptive multi-issue RAG (P0 + P1)

`ADAPTIVE_RAG_ENABLED=true` enables full-input routing. Single questions keep the
existing retrieval/prompt path. Long cases, numbered questions, multiple dates,
transactions or parties use a Pydantic-validated plan. Invalid, incomplete or
timed-out plans fall back to contiguous groups of the complete original input.
The original question is always retained for the answer.

Planned queries send the optional `planned=true` flag to skip heuristic splitting
in RAG. Search accepts up to 20,000 characters without silently clipping the
original input. Cache keys include the planned flag and retrieval implementation
version so older cached rankings cannot mask the new pipeline.

`CASE_MAX_RETRIEVAL_QUERIES` bounds the total queries across both rounds. If this
budget is exhausted, uncovered issues remain explicit in metadata and the prompt.
The 90-second deadline includes planning, retrieval, grading and the answer/SSE.
Evidence packing uses UTF-8 byte length as a conservative tokenizer-independent
token upper bound; it includes source labels and separators and may underfill the
model's actual token budget.

Case retrieval uses at most `CASE_MAX_RETRIEVAL_QUERIES` requests in total,
including the optional supplementary round. Concurrent requests are limited by
`CASE_RETRIEVAL_CONCURRENCY`. Candidate admission interleaves issues before the
global cap. Relevance runs per issue in batches of twelve with keyword-centered
previews. Failed grading retains that issue's candidates and marks degradation.
Supplementary retrieval failure retains already selected evidence.

Selection prefers uncovered issues, deduplicates evidence within a document,
and enforces both the chunk cap and evidence token budget. Token accounting uses
UTF-8 byte length as a conservative tokenizer-independent upper bound, including
source headers and separators. `context_tokens` records this upper bound, not
provider-billed usage. Planner, grading and answer usage are added in
`tokens_used` when reported by the providers.

Complex answers use `[S1]` source IDs mapped to chunk/document/section and issue
metadata. `missing_issue_ids`, degradation flags, retrieval rounds and query count
remain visible in metadata. The shared deadline includes planning, retrieval,
grading and the answer, for both regular and streaming requests. Stream options
include the profile's output cap. Provider length limits produce
`truncated=true` and `finish_reason=length` while preserving partial text.

RAG search accepts up to 20,000 characters, without silent truncation. Each hybrid
query combines independent BM25 over eligible corpus text, vector results, and
exact number/article matches with RRF (`k=60`). Lexical/exact reads use fresh
sessions and the same active/processed/model/type filters as vector search.
`HYBRID_ENABLED=false` selects vector-only retrieval. Legacy `HYBRID_ALPHA` and
`HYBRID_POOL` only control the retained offline rerank helpers.

All adaptive defaults are declared in agent Settings, its `.env.example` and
Compose. `CASE_MAX_RETRIEVAL_ROUNDS=2` means the initial round plus at most one
supplement. The query and total deadline budgets may stop supplementation sooner.
Setting `ADAPTIVE_RAG_ENABLED=false` restores the previous agent pipeline.

Validation commands (run from each service directory):

```powershell
python -m pytest tests/
```

New tests cover full-input routing/fallback, tail preservation and cache keys,
bounded concurrency, issue grading and previews, evidence coverage/budgets,
supplementation/deadlines, provider truncation and SSE options/metadata, independent
lexical recall, exact lookup filters, session isolation and RRF.

Validation in the implementation session: both exact commands were attempted from
their respective service directories. Both exited 1 before collecting tests
because PowerShell could not find `python`; AppData's Python directory was also
inaccessible. No passing pytest result is claimed. Run both suites with the
project's accessible Python interpreter before accepting/deploying the change.

The implementation does not change embeddings or ingest existing documents.
Historical effective-date/version retrieval and expert-labeled legal accuracy
benchmarks remain outside P0 + P1.
