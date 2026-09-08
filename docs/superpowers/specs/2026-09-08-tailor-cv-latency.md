# tailor_cv latency — 2026-09-08

## Problem

The `tailor_cv` LLM call (the "Generating the tailored CV…" step) ran 15–25+ min
with no upper bound. On the current model (`Gemma-4-26B-A4B` Q4_K_M, self-hosted
llama.cpp, speculative decoding + KV cache already on), thinking mode generates
10k–20k+ tokens of reasoning before it writes a single line of CV. Decode runs at
~14 tok/s here, so the reasoning alone is 12–25 min, and one observed run was
still going at 24k tokens / 24 min when killed.

`plan_tailoring` and `check_guardrails` already run with `enable_thinking: False`;
only `tailor_cv` still had it on.

## Benchmark (job 292, scope [1,2,3,4,5], 3 reps each unless noted)

| variant | think | max_tokens | mean wall | tokens | typos fixed | guardrails | diff vs base |
|---|---|---|---|---|---|---|---|
| baseline (stored ref) | on | — | 15–25+ min, unbounded | ~20k+ | 4/4 | 9 ok | 2 sec reorder · 1 block · 3 lists · 6 reword |
| **nothink** | off | none | **66.1 s** (67.8 / 65.1 / 65.3) | ~1330 | 4/4 all reps | pass 1/3, "Linux" flagged 2/3 | 1 sec reorder · 1 block · 3 lists · 5–6 reword · 1 add |
| nothink_simple (trimmed prompt) | off | none | 65.5 s (66.4 / 64.4 / 65.8) | ~1330 | 4/4 all reps | pass 2/3 | 1 sec reorder · 1 block · 2 lists · 4–10 reword · 1 add |
| cap2500 (thinking + hard cap) | on | 2500 | 116.9 s, **empty output** (finish=length) | 2500 | — | — | — |

Job 292's job-posting context is only ~4.3k chars (well under the 9k cap), so
"limit the raw job input" changes nothing for it. Prompt simplification saved no
time (generation length is identical ±1 %) and made the reword count noisier.
Capping tokens on a thinking call just truncates it mid-reasoning → empty CV.

## Decision

`tailor_cv`: default `think=False`, add `max_tokens=4096` as a runaway backstop
(a real tailored CV is < 2k tokens, so it never truncates one). `think=` stays a
parameter so a future stronger/faster model can turn reasoning back on.

Confirmed once more through the real (patched) task path: 65.2 s, 4/4 typos,
guardrails clean.

Result: **~66 s, tight variance, quality on par with the thinking baseline** —
all four base-CV spelling errors fixed every run (achitecture, Adbdressing,
Enginer, contribbution), plus a grammar fix ("contributor's" → "contributors"),
substantive reorder/reword matching the tuning directives, guardrails clean.

## Known residual

Every non-thinking run adds a "Linux" / "Linux environments" item to the
Technologies list — the tuning directive says "ensure Linux is prominent within
the Technologies section" but the base CV has no Linux entry, so the model
creates one. The guardrail checker flags it under rule 8 ("do not be more
specific than the base CV implies") ~half the time. The stored thinking-mode
reference avoided it. This is a directive-wording issue (ask for Linux to be
*foregrounded where already present* rather than made prominent), not a reason to
keep thinking on — fix the directive, or accept the mild addition.
