# Tabula v12 — PLAN: native bounded thinking (inference API + sonnet)

> Status: **plan only — not yet minted.** This is the endpoint/model track,
> kept SEPARATE from the model-agnostic releases on the current stack (haiku,
> Agents/inference API). v12 is minted as a fork of the then-current champion
> (v11 — the pyramid/posture comprehension release — if its fixes win), and this
> plan is executed on top of it.
>
> Versioning history: drafted as "v8", moved to "v9", "v10", "v11", and finally
> to **v12** so the model/endpoint migration never gets merged into the
> model-agnostic fixes. v9 delivered comprehension (won seed 69); v10 delivers
> deterministic compiler-faithful execution; **v11 unifies harvester assignment
> under the Pyramid×Posture model + grounded memories**; **v12 is the heavyweight
> model bet** (this doc — renamed from v11 when the pyramid work took the v11 slot).

**Goal.** Deliver *genuinely deeper reasoning* — turn last-night understanding
and novel trade-offs into changed behaviour — using Claude's **native extended
thinking** in a **single call** at high fidelity (reasoning stays latent,
in-context, when the model writes the answer). This is the heavyweight
counterpart to the cheap two-call haiku split used by v7–v11.

**Why v12 exists (vs v7–v11).** v7 gets channel separation and a reasoning pass;
v8 improves inputs + doctrine; v9 adds comprehension; v10 makes execution
deterministic; v11 unifies harvester assignment (Pyramid×Posture) + grounded
memories — but ALL of them still run `haiku` and pay a serialize→re-read seam
between reasoning and moves. v12 spends more (bigger model, endpoint migration,
more wallclock) to buy actual reasoning depth and a zero-seam handoff — the
strongest lever for decisions NOT covered by precomputed hints (chaff
anticipation, novel trade-offs). It rides on top of v11's faithful executor +
pyramid menu: the thinker just gets smarter; the packager still guarantees
correctness.

---

## Carried-over goals

- **G1 — Chaff-reactive planning.** Anticipate chaff from opponent stock +
  prior-night losses and pre-empt it (shorter chains, earlier pickups, safer
  drop sites). In v12 this is *reasoned*, not just hinted.
- **G2 — Grounded targeting.** Tie chain/probe choice to prior-night outcomes
  and current highest-EV RED.
- **G3 — Native bounded thinking** as the reasoning engine that powers G1/G2.

Note: the *mechanical* hints, richer inputs, comprehension digest, pyramid menu,
and the deterministic packager are model-agnostic and already exist in v8–v11.
v12 adds only the reasoning layer on top — do not rebuild them here.

## The core change: migrate off the Agents API to the inference API

Today the harness calls the Cortex **Agents** endpoint
(`/api/v2/databases/.../agents/{name}:run`) with server-side `CREATE AGENT`
specs. That API exposes **no** thinking knob. Bounded thinking lives on the
Cortex **inference** REST API:

- **Chat Completions** (OpenAI-compatible): `/api/v2/cortex/v1/chat/completions`
  - `reasoning: { max_tokens: N }`  ← **the bounded thinking sub-budget**
    (or `reasoning: { effort: low|medium|high }`)
  - `max_completion_tokens: M`      ← the answer budget
- **Messages** (Anthropic-compatible): `/api/v2/cortex/v1/messages`
  - `thinking: { type: "adaptive" }` + `output_config.effort` (max/high/medium/low)

**Model.** Adaptive thinking is documented for `claude-opus-4-6`+ and
`claude-sonnet-4-6`; **`claude-haiku-4-5` is not listed as thinking-capable.**
Target `claude-sonnet-4-6` (quality/latency balance) with opus-4-6+ as a later
option.

## The haiku-vs-sonnet head-to-head

Because both models run on the SAME inference endpoint once migrated, v12 is
where we can finally price the reasoning upgrade cleanly:

- **v12-haiku** (`claude-haiku-4-5`, no/low reasoning budget) vs
  **v12-sonnet** (`claude-sonnet-4-6`, bounded `reasoning.max_tokens`),
  both on the identical v11 data + doctrine + pyramid menu + packager.
- This isolates the model/reasoning lever from the data/doctrine/execution lever
  (already validated by v8–v11). Win-rate delta vs wallclock + token cost decides
  whether sonnet's thinking is worth the premium.

## Work items

### A. Invoker: add an inference-API path
- New request-body builder: `model`, `messages` (our prompt as system + user),
  `reasoning: {max_tokens: N}`, `max_completion_tokens: M`, `stream: true`.
- Same PAT auth; new base path `/api/v2/cortex/v1/chat/completions`.
- **SSE parsing branch** for the chat-completions shape: answer tokens in
  `choices[].delta.content`; reasoning tokens in the reasoning field
  (`choices[].delta.reasoning` / `reasoning_details`). **Reuse the split
  principle already shipped** (`sea_of_colours/agent/cortex_invoker.py`): route
  reasoning to the audit-only buffer, excluded from the byte cap + completion
  predicate; only the answer is capped + predicate-checked. This is an *added
  branch*, not a rewrite — the Agents path stays for v1–v11.
- Bounded thinking = `reasoning.max_tokens` forces the handoff to the answer;
  the wallclock cap remains the hard backstop.

### B. No server-side agent spec — move instructions inline
- The `instructions.orchestration` / `instructions.response` currently in the
  SQL become an inline **system message** (the moves-first response contract
  must be preserved verbatim).
- We drop the Agents tool loop — tabula uses no tools, so this is a non-issue.
- Keep all harness-side machinery unchanged: prompt builder, hints, grounded
  reflection, deterministic packager, heuristic net.

### C. Preserve moves-first under the new model/endpoint
- Keep `{"moves":[` first-byte rule + reason-in-head + completion predicate.
- Re-tune the answer byte cap for sonnet (may differ from haiku's 8000).
- Consider `response_format: {type: json_schema}` (chat completions supports it)
  to hard-constrain the moves JSON — validate it doesn't fight moves-first order.

### D. Re-validation (the risky part — must gate rollout)
1. **Wallclock.** sonnet + thinking is slower than haiku. Measure p50/p95 of
   (thinking + answer) under `reasoning.max_tokens` candidates (e.g. 2k/4k/6k).
2. **Cost.** sonnet + thinking tokens are pricier; quantify per-season.
3. **Quality.** Does the reasoning actually change moves (chaff pre-emption,
   better targeting) vs v11? Audit the thinking traces.
4. **Truncation/fallback rate** vs v11.

### E. Observability
- Persist the isolated thinking trace + answer to `SOC_AGENT_INVOCATION` using
  the labelled multi-row sub-invocation mechanism (thinking already surfaced in
  the invoker return envelope). The reasoning viewer renders the trace + answer
  with no extra frontend work.

## Validation plan

- **Single-seed spike first**: one seed, measure wallclock p50/p95, truncation
  rate, and eyeball decision quality vs v10. Kill or continue on the numbers.
- Then full seed set, four-way A/B:
  **v6 (baseline) vs v11 (champion) vs v12-haiku vs v12-sonnet**, plus seed 69.

## Success criteria

- Beats v11 on the seed set (incl. seed 69).
- Reasoning traces show genuine chaff anticipation / targeting logic that
  demonstrably changes the moves (not post-hoc rationalisation).
- Wallclock within the accepted ceiling (or a deliberately raised ceiling
  justified by the quality gain).

## Risks

- **Wallclock overrun** (sonnet + thinking) → bound via `reasoning.max_tokens`
  + effort=low/medium; may need a higher ceiling → product decision.
- **Cost** → sonnet + thinking premium; only worth it if quality clearly wins.
- **Endpoint parity/observability** → new SSE shape; reuse existing audit
  plumbing; the Agents-API niceties (server-side specs, tool loop) are gone.
- **Two code paths in the invoker** → keep the inference branch cleanly isolated
  and well-tested so v1–v11 (Agents path) are untouched.

## Relationship to v7–v11

- v7 (cheap split), v8 (data + doctrine), v9 (comprehension), v10 (deterministic
  compiler-faithful execution), v11 (Pyramid×Posture harvester assignment +
  grounded memories), and v12 (native thinking on the inference API) are
  **layered bets**. The first five are model-agnostic and land first; v12 is the
  heavyweight model/endpoint experiment on top of them.
- If v12's fidelity/quality gain beats its latency+cost, it supersedes. If not,
  v11 stays champion and v12 is shelved with its findings recorded here.
- Shared groundwork already done: the invoker thinking/answer channel isolation
  and its tests (`tests/test_cortex_invoker_thinking.py`); the labelled
  multi-row reasoning viewer.
