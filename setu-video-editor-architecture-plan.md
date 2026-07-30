# Setu → AI Conversational Video Editor: Implementation Blueprint (v2)

This supersedes the v1 architecture document. Same investigation of the codebase underlies it; this version folds in your refinements and is structured as a phase-by-phase build plan rather than a pure architecture description. Where something is unchanged from v1 it's stated briefly with a pointer to why; new/changed material is expanded.

## Changelog from v1

- **Planner output is now a strict schema** — a list of `{stage, params}` objects, not a bare stage-name list plus a side-channel params dict. A new explicit **validation/translation layer** turns this into Setu's native `workflow: list[str]` + `payload` shape before it ever reaches `JobSubmissionService`. (§6)
- **Notification Worker removed for V1.** Frontend polls `GET /jobs/{id}`, which already exists and already returns everything needed. The terminal-lifecycle-event idea from v1 is demoted to an optional backlog item, not a V1 requirement. (§9, §10, §20)
- **Roadmap rewritten** as small, independently-demoable phases, each with Goal / Components / DB changes / API changes / Worker changes / Frontend changes / Testing strategy / Demo scenario / Risks. (§19)
- Everything else — the two-job split, memory design, storage-first sequencing, "reuse Setu unmodified" posture — is confirmed as-is from v1 and carried forward.

## Changelog from v2

- **Phase 5 (editing workers) is no longer one phase.** It's now five sequential sub-phases — Crop, Color, Audio, Subtitle, Export — each fully implemented, tested, and demoed before the next starts. The system stays in a working, demoable state after every single worker. (§19, Phase 5a–5e)
- **Memory updates are no longer triggered from `GET /jobs/{id}`.** GET endpoints stay read-only, no hidden side effects. The frontend now explicitly calls `POST /jobs/{id}/update-memory` once it observes a job has completed. The endpoint is idempotent. (§17, §19 Phase 6)

## Changelog from v3

- **Phases 8 and 9 added** (2026-07-24): Phase 8 — Collaborative Project Rooms (multi-user shared workspace, project-scoped conversations, WebSocket sync); Phase 9 — AI Collaboration & Proposal Workflow (multi-participant-aware planner, proposals, approval policies, task ownership + cancellation). (§19)
- **The "Setu core stays unmodified" rule is relaxed to a strong default.** Phases 0–7 treated it as hard; from Phase 8 on, Setu may be modified when there's a genuinely good reason, documented at the point of change. Phase 9b's cooperative cancellation is the first deliberate exception. (§12, §19 Phase 9b)
- **Cancellation moves out of the backlog** into Phase 9b. (§20)

## Changelog from v4

- **Phase 9 split into 9a and 9b**, since it had grown to bundle three separable concerns: planner facilitation + proposal data model, approval-policy evaluation, and a Setu-core change (cancellation). **9a — Proposals & Approval Workflow** (planner, proposals, approval policies) needs zero Setu changes and is independently demoable. **9b — Cooperative Cancellation** is the one justified Setu-core change, isolated on its own so it can be reviewed/tested/demoed in isolation from the approval mechanics. (§19)
- **Approval policies redefined as `admin` / `team`**, replacing the earlier `owner` / `majority` / `unanimous` draft. `team` (unanimous, every active member) is **the V1 default** — chosen over `admin` because it fits this product's small-creative-team framing, at the cost of the reject/re-discussion lifecycle it requires (below). `majority` moves to the backlog alongside newly-added future policies (Selected Reviewers, Two-Level Approval, Custom Approval Rules). The `admin` policy name also deliberately replaces `owner` to stop it colliding with **job ownership** (the separate, unrelated concept used for cancellation authorization) — same word, two different decision-makers, worth naming distinctly. (§19 Phase 9a)
- **Proposal rejection is no longer a dead end.** Under `team`, any single reject immediately ends the proposal (unanimity is already impossible — no need to wait out the rest of the vote) and returns the room to open discussion; the planner may then produce a revised proposal as a new row, with the rejected one kept as an audit record. (§19 Phase 9a)
- **The planner is now approval-policy-aware** — it renders the room's active policy in its prompt context and phrases facilitation messages accordingly (e.g. "ready for admin approval" vs "waiting for approval from all team members"). This is wording only: the planner still never decides approval outcomes or executes workflows, only produces proposals/messages — that stays the collaboration layer's job. (§19 Phase 9a)

## Changelog from v5

- **Project is now the aggregate root, effective immediately rather than at Phase 8.** `Project` was originally going to be introduced by Phase 8 (Collaborative Project Rooms); it actually landed when Phase 2 was implemented (`Conversation` was parented to `Project`, not `Video`, from the start — see the Phase 2 section) and was then extended to `Video` as a deliberate follow-up migration. Every video now belongs to exactly one project; there is no such thing as an orphan video. (§14, §19 Phase 1, Phase 8)
- **`POST /videos` is gone, replaced by `POST /projects/{id}/videos`.** `GET /videos/{id}` is unchanged (reading by a video's own id is still a reasonable flat lookup) but now returns `project_id`. A new `GET /projects/{id}/videos` lists a project's videos. (§19 Phase 1)
- **`videos.project_id` and `conversations.project_id` are both `NOT NULL`** (the latter also `UNIQUE` — one shared conversation per project). Phase 8's original text describing a *nullable* `project_id` gained "later" is corrected in place — nullable-then-backfilled was never how this shipped. `videos.project_id` also changed from `ON DELETE SET NULL` to `ON DELETE CASCADE`: deleting a project deletes its videos, matching "no orphan video" above. (§14, §19 Phase 8)
- **This confirms the intended product UI flow end to end**: create a project → upload video(s) into it → invite members (Phase 8) → members converse (Phase 2, then Phase 9a once proposals exist) → approved proposal executes the edit (Phase 3/4). Nothing here is a new phase — it's the mapping the phases below already implement.
- **Multiple videos per project already works** — `videos.project_id` was never unique, unlike `conversations.project_id`. What's *not* built yet: a user-entered display name per video, distinct from the uploaded file's name. Small, additive, deliberately deferred (see Phase 1's Risks).

## Changelog from v6

- **AI architecture principles added** (2026-07-26, before Phase 3 started): a new subsection under §5 sets explicit constraints for Phase 4 onward — single stateless planner (no multi-agent frameworks), ordinary services rather than an agent abstraction, no memory beyond §7's `user_preferences`, no premature conversation-summarization, general complexity discipline (stop and explain before adding orchestration/memory a phase seems to need). Documentation only — no code changed. (§5)
- **Stale `"plan"` terminology fixed throughout** — §5, §6, §13, Phase 2, Phase 3 (including its heading), Phase 4, and Phase 5a all still said `{"type": "plan", ...}` / "plan validator" / "a plan" even though the actual Phase 2 implementation (`StaticPlanner`) already ships `{"type": "proposal", ...}`, precisely to avoid a rename at Phase 9a. This was drift between the doc and shipped code dating back to Phase 2, not a new decision — now corrected to match what's actually running. Phase 9a's text describing the discriminator "evolving" from `plan` to `proposal` is also corrected: that shape was already `proposal` from Phase 2, so Phase 9a only adds two new fields (`reasoning`, `discussion_summary`) to the existing shape, not a rename.

## Changelog from v7

- **Phase 3's design revised before any code was written** (2026-07-27), for long-term extensibility across the AI planner, multiple editing stages, collaboration, and multiple assets — while keeping Phase 3 itself simple, per the AI architecture principles (§5). "Translator" renamed **"compiler"** throughout (§6, Phase 3, Phase 4, Phase 9a) — its future responsibilities (asset resolution, param normalization, default injection, shorthand expansion) are closer to compilation than plain translation; stays a pure function regardless of name. The module-level `CAPABILITY_REGISTRY` constant becomes a small `CapabilityRegistry` class (`get`/`exists`/`list`/`register`) so the validator depends on an interface, not a global — still backed by a hardcoded dict for V1. Raw dicts inside the validation/compilation pipeline become `Proposal`/`ProposalStage` frozen dataclasses (matching `StageMessage`'s existing convention), explicitly **not** touching `planner.py`/`ConversationService`, which still pass plain dicts until Phase 4 does that conversion for real. The validator returns a `ValidationResult` (collecting every error, not just the first) instead of only raising, since Phase 4's regenerate-and-retry loop needs the full error set to feed back into the LLM prompt. `video_uri` moves into a one-field `ExecutionContext` dataclass alongside the `Proposal`, keeping the compiler's signature stable as more context needs to travel with it later. Duplicate stage names in one proposal are rejected by the validator (a deliberate V1 policy, not a Setu constraint — Setu's engine would mechanically tolerate them). (§19 Phase 3)
- **`StageCapability` deliberately does not gain speculative fields.** `parameter_schema` (renamed from `params`) and `description` are kept; `worker_name`, `category`, `version`, `supports_batch` were proposed but declined for now — `worker_name` would duplicate the `stage name = topic name = WORKERS key` identity §6 already establishes, and the other three have no consumer anywhere in Phase 4 or Phase 5's plan as written. Each gets added in whichever sub-phase first actually needs it. (§19 Phase 3)

## Changelog from v8

Architectural refinement pass before Phase 4 implementation started (2026-07-27), user-directed, documentation-first per the same discipline as v6/v7 — no Phase 4 code existed yet when this was written.

- **`Planner`'s interface signature changes** — this is the largest single deviation from a literal reading of the refinement brief, and it's a *consequence* of two of the brief's own items, not an independent choice: item 3 introduces a `PlannerContext` model specifically to replace "many unrelated arguments" into the planner, which is incompatible with item 1's `respond(messages, preferences)` shape surviving unchanged. `Planner.respond` becomes `respond(context: PlannerContext) -> PlannerResponse` — both the input *and* the output are now typed domain objects, not `dict[str, Any]`, per item 4's "avoid passing raw dictionaries throughout the application" (a `dict` return would have been a half-measure of the same principle). `StaticPlanner` moves to the new signature too, since it implements the same interface; its Phase 2 tests move with it. `ConversationService` is the only caller and absorbs the new context-assembly + serialization responsibility (see below) — this is exactly the seam Phase 2's own docstring anticipated ("Phase 4 swaps StaticPlanner for a real LLMPlanner ... nothing about this sequencing changes"), it just turned out the *signature* changes even though the *sequencing* doesn't.
- **New services introduced, each a plain stateless function/class per the v6 principles — not an agent abstraction:**
  - `PlannerContext` (`backend/services/planner_context.py`) — one dataclass carrying everything a planning call needs: project, conversation history, the project's videos, user preferences, the capability registry, and an `approval_policy` field that's a placeholder today (unused until Phase 9a) so that phase doesn't need to touch the planner's context shape again.
  - `PromptBuilder` (`backend/services/prompt_builder.py`) — `PlannerContext → Prompt`, extracted so future prompt types (clarification, revision, summarization) can reuse the same construction step without duplicating it inside `LLMPlanner`.
  - `PlannerResponse` (alongside `Proposal`/`ProposalStage` conventions from Phase 3) — `type` / `message` / `proposal`, where `proposal` is exactly Phase 3's `Proposal` (no new proposal shape).
  - `LLMClient` (`backend/services/llm_client.py`) — a small abstraction so `LLMPlanner` never imports a provider SDK directly. **Only one concrete implementation ships now: `GroqClient`.** Stub classes for other providers (OpenAI, Anthropic, etc.) are explicitly *not* pre-built — per the v6 "no speculative generality" discipline, an interface plus its one real caller is enough until a second provider is actually being integrated.
- **Groq is the V1 provider**, using its OpenAI-compatible `response_format={"type": "json_schema", ...}` mode (verified against the installed `groq` SDK — real schema-enforced structured output on supported models, not prompt-engineered JSON; §item 6's requirement holds). Model name is a `Settings` field, not hardcoded, so switching within Groq's supported-models list needs no code change.
- **Retry strategy is two-layered and bounded.** Infrastructure retry covers network failures, provider timeouts, and malformed (non-parseable) structured output. Semantic retry is separate and stays to *exactly one* regeneration: if `validate_proposal` (Phase 3, untouched — see below) rejects the LLM's proposal, the planner regenerates once with the `ValidationResult` errors fed back into the prompt; if that regeneration still fails validation, the user gets a friendly clarification message instead of a job. No recursive retries, no reflection/self-critique loop, no unbounded regeneration — this is the literal gate v6 principle 1 calls for at Phase 4, applied.
- **`validate_proposal` and `compile_workflow` are reused as-is where possible; `compile_workflow`'s input/output shapes change, its responsibility doesn't.** Phase 3's validator logic (unknown-stage / unknown-param / type-mismatch / duplicate-stage checks) is untouched code. What changes, and why (see Multiple Videos below): `ExecutionContext.video_uri: str` → `ExecutionContext.video_uris: dict[str, str]`, and `ProposalStage` gains `video_ids: list[str]`. This is a data-shape change, not a new compiler responsibility — `compile_workflow` still does only `Proposal → Workflow`, it just resolves per-stage video handles into URIs while doing it, which stays a pure dict lookup rather than a general asset-resolution system.
- **Multiple videos are in scope for Phase 4, reversing an earlier draft of this same refinement pass that said to keep a single target video until Phase 5.** The reversal is deliberately narrow: a project already holds many `Video` rows (no unique constraint, since Phase 1/2) so the planner already needs to choose among several when building a proposal — Phase 4 makes that explicit at the model level rather than pretending it isn't true yet. `ProposalStage.video_ids` uses short LLM-facing handles (e.g. `"video_1"`, not raw `Video.id` UUIDs — models are unreliable at echoing UUIDs verbatim) that `PromptBuilder` assigns from `PlannerContext.videos` and `ConversationService` resolves back to real storage URIs when building `ExecutionContext`. This is **not** the generic `Asset`/`AssetRegistry`/`AssetResolution` system deferred below — it's a video-specific id→uri dict, already known data, no resolver abstraction.
- **Known landmine, deliberately not fixed in Phase 4:** `validate_proposal`'s duplicate-stage-name rejection (`backend/services/proposal_validator.py`, keyed only on `item.stage`) will reject a legitimate future workflow like `crop(video_1) → crop(video_2) → combine(video_1, video_2)`, since `"crop"` appears twice regardless of differing `video_ids`. This can't actually trigger in Phase 4 (the registry only has `"dummy"` registered — no `crop`/`combine` exist yet), so item 7's "don't change the Phase 3 validator" holds for this phase. Phase 5, when it introduces per-stage workers, will need to change the uniqueness key from `stage name` to `(stage name, video_ids)`. Recorded here so it's a planned Phase 5 change, not a surprise.
- **`POST /projects/{id}/confirm-proposal`** is the new, explicit confirmation entry point — free-text chat confirmation is deliberately not supported. It scans the conversation backwards for the most recent `{"type": "proposal"}` message (not just the last message — a clarifying turn can follow a proposal), compiles it, and submits via the existing `JobSubmissionService.submit()`. **Idempotency reuses Setu's existing idempotency-key mechanism (Phase 1) rather than adding a new persisted "already confirmed" flag** — the key is derived deterministically from the conversation id and the proposal message's id, so calling the endpoint twice replays the same `Job` instead of creating a second one. This satisfies the "no proposal persistence" deferral below: nothing new is stored, and the compiled `(workflow, payload)` must remain a pure function of the proposal + video rows (no fresh timestamps or regenerated URIs) for the idempotency-key hash to match on replay.
- **Observability is additive logging only** around planner execution (provider, model, latency, prompt/completion tokens, whether regeneration was used, validation success/failure) — no tracing/metrics redesign.
- **Deferred to Phase 5** (explicitly, so Phase 4 doesn't reach for them early): richer `StageCapability` fields (`category`, `version`, `supports_batch`, execution hints) beyond what the planner already needs; any `compile_workflow` responsibility beyond `Proposal → Workflow` (param normalization, macro expansion, workflow optimization); the generic `Asset`/`AssetRegistry`/`AssetResolution` system (Phase 4 keeps using `Video` rows directly, just referenced by more than one per proposal now); redesigning worker/capability metadata ahead of Crop/Color/Audio/Subtitle/Export actually landing.
- **Deferred to Phase 8/9**: proposal persistence, proposal version history, proposal revisions, a real approval engine, collaborative proposal editing, proposal ownership, multiple proposal versions, and any `proposals`-table-shaped DB model beyond what Phase 4 needs (which is none — see confirm-proposal above).
- **General posture note, generalizing the v3/v4 changelog's "Setu core unmodified is a strong default, not absolute, from Phase 8 on":** that posture applies at *any* phase when there's a genuinely good reason, including Phase 4, despite Phase 4's own stated philosophy of "nothing in Setu's execution engine changes." In this pass specifically, no Setu-core change was needed — `Job.payload` is an opaque `dict[str, Any]` never inspected by `JobSubmissionService`/`StageProcessingService`/the workflow engine, so the `ExecutionContext`/`ProposalStage` reshape above is entirely contained to this project's own Phase-3-owned files. Recorded as a posture clarification, not because this pass needed to exercise it.

---

## 1. General direction (confirmed, unchanged)

Conversational video editing agent on top of Setu. Setu remains the execution engine. The LLM plans; it never edits. Workers stay deterministic. No change from v1 — restated here because every later decision depends on it.

---

## 2. Architecture shape (confirmed, unchanged)

```
Conversation Layer
      ↓
   Planner
      ↓
Execution (Setu — unmodified)
```

The conversational layer decides *what* to run; Setu decides *how* it runs reliably. This separation is preserved exactly, including through the phase breakdown in §19 — no phase collapses planning and execution together.

---

## 3. Two independent jobs (confirmed, unchanged)

```
Job #1: upload → Video Analysis Worker → metadata stored
                          ↓
        Conversation → Planner → user confirms
                          ↓
Job #2: Edit Workflow (crop, color, audio, subtitle, export, ...)
```

Not merged. Analysis metadata is stored against the **video**, not the job, so it's reusable across any number of later edit jobs against the same upload (e.g. "now make me a TikTok version" doesn't re-run analysis). This was the key structural call in v1 and it stands.

---

## 4. Conversation architecture (simplified per your note)

`messages` table only. A chat turn reads the most recent N messages for the conversation, verbatim, and sends them to the planner as-is. No summarization, no compaction, no semantic retrieval, no vector store. If the history ever gets too long for a prompt, the fix is "send fewer, more recent messages" — not a new subsystem. This is intentionally the simplest thing that works, not a placeholder for something more sophisticated later.

---

## 5. Planner architecture

Still a synchronous service in the API process, never a Kafka worker. Inputs:

1. Conversation — most recent messages, raw (§4)
2. User preferences — the `user_preferences` row for this user (§7)
3. Video metadata — the analysis result attached to this video (§8)
4. Available worker capabilities — the capability registry (§6)

The planner decides which workers to run, in what order, and with what parameters. It never executes anything — no ffmpeg, no OpenCV, no Kafka access, no video bytes.

Because the planner sometimes needs to keep asking questions before it has enough information to propose anything ("Who is this video for?"), its response needs one more bit of structure than the proposal schema alone: a top-level discriminator.

```json
{ "type": "message", "text": "Who is this video intended for?" }
```
or
```json
{ "type": "proposal", "summary": "...", "workflow": [ ... ] }
```

The `"proposal"` case's body is exactly the schema you specified — see §6. This discriminator is the one addition beyond what you wrote; flagging it explicitly rather than silently introducing it, since it's necessary for the "assistant asks clarifying questions before proposing anything" behavior in the spec. (Named `"proposal"` rather than `"plan"` from the start, anticipating Phase 9a's approval workflow — see the principles below and the Phase 9a section; this also matches what actually shipped in Phase 2's `StaticPlanner`.)

---

## AI architecture principles (v6)

Added 2026-07-26, before Phase 3 started, as explicit guidance for Phase 4 onward — nothing below changed any shipped code; it constrains how future phases get built.

1. **Single planner, no multi-agent frameworks.** One LLM call producing one structured response (§6). No LangGraph, CrewAI, reflection loops, planning graphs, or autonomous tool routing, unless a future phase's actual requirement clearly can't be met without one — and if that seems to be happening, **stop and explain the reasoning before implementing**, don't just reach for the framework. This is the literal gate to apply at Phase 4 and Phase 9a specifically — those are the two phases where prompt complexity could tempt one.
2. **Stateless planner.** Every invocation gets all its context explicitly passed in (project metadata, uploaded videos/assets, conversation history, user preferences, approval policy where relevant, the existing proposal when revising one) and returns a structured response and nothing more. No hidden state, no session carried inside the planner itself between calls.
3. **Ordinary services, not agents.** The flow is plain backend services calling each other — prompt/context construction, the LLM call itself, and the Phase 3 validator — not an "agent" abstraction. Phase 4's Components list already describes this flow (prompt construction from conversation + preferences + metadata + registry → call the LLM → parse the response, then the Phase-3 validator gates the result); read informally as roles rather than a required class split, that's context-building = the prompt-construction step, planning = the LLM call/parse step, validation = Phase 3's validator, unchanged either way.
4. **No sophisticated memory.** No vector/semantic/episodic memory, no RAG. §7's `user_preferences` table — one flat row per user, written by one optional lightweight LLM call after a successful edit — is already the full extent of memory for V1, and already satisfies this principle as shipped; nothing there needs to change.
5. **No premature context-length optimization.** Typical project discussions are expected to fit comfortably in modern context windows; summarization machinery is deferred until real usage actually demonstrates a need. Phase 2's `conversation_context_limit` (fixed at 20 recent messages, already shipped) is the simple cap this principle is fine with, not the kind of premature optimization it warns against — don't read this principle as a reason to rip that cap out.
6. **General complexity discipline.** Prefer the simplest architecture that solves the current phase's actual requirement. If, while implementing a future phase, it looks like more orchestration or memory than described above is genuinely needed, stop and explain the reasoning before implementing it — don't add it silently.

---

## 6. Planner output schema (new — formalized per your request)

Canonical shape for the `"proposal"` case:

```json
{
  "summary": "Remove the silent intro, brighten slightly, normalize audio, add captions, export 1080p.",
  "workflow": [
    { "stage": "crop", "params": { "aspect_ratio": "9:16" } },
    { "stage": "color", "params": { "brightness": 1.1 } },
    { "stage": "audio", "params": { "normalize": true } }
  ]
}
```

**Validation, before this touches `JobSubmissionService` at all:**
1. `workflow` is non-empty.
2. Every `stage` name exists in the capability registry (§ below) — an unregistered stage name is rejected outright, never silently passed through.
3. `params` for each stage are checked against that stage's declared parameter shape in the registry (at minimum: known keys, correct types — doesn't need to be a full JSON-Schema validator for V1).
4. Any failure here does **not** reach Setu as a bad job — it's handled in the conversational layer as either a regenerate-and-retry or a clarifying question back to the user ("I'm not sure what aspect ratio you want — could you clarify?").

**Translation into Setu's native shape**, which is what actually gets submitted:

```python
workflow = [item["stage"] for item in proposal["workflow"]]          # -> Job.workflow
stage_params = {str(i): item["params"] for i, item in enumerate(proposal["workflow"])}
payload = {"video_uri": ..., "stage_params": stage_params}           # -> Job.payload
```

(Phase 4 onward: `video_uri` here becomes per-stage `video_uris`, once a proposal can reference more than one video — see Changelog v8 and §19 Phase 4. `Job.payload` stays an opaque dict either way; Setu's core never inspects its shape.)

This is the same "index-keyed stage params live inside the existing freeform `payload`" idea from v1, just now explicitly framed as a compilation step with the planner's schema as its input — the planner never has to know or care that Setu's `Job.workflow` is a flat string list. **This compilation function is the one new piece of code between "planner" and "Setu"** (§19 Phase 3 names it `compile_workflow`, replacing an earlier "translator" working name). Everything on the Setu side of it (`JobSubmissionService.submit(workflow=..., payload=...)`) is called completely unmodified.

**Capability registry** (new, static, lives in code — not a DB table): for every registered worker, its stage name (= topic name = `WORKERS` dict key, same convention Setu already uses), a short description for the LLM prompt, and its accepted parameter keys/types. This is both the LLM's prompt-time contract ("here's what you're allowed to choose from") and the validator's runtime contract ("here's what's actually allowed") — one source of truth for both.

---

## 7. Memory architecture (confirmed, unchanged)

```
user_preferences(
  user_id PK,
  preferred_platform, preferred_export_format, captions_enabled,
  preferred_resolution, subtitle_language, updated_at
)
```

Loaded before every planning call. Written to only after a successful edit, via one optional lightweight LLM call over the conversation ("anything durable worth remembering here?"). No vector DB, no framework, nothing beyond this table. Where this write gets triggered from, given there's no Notification Worker, is addressed in §19 Phase 6.

---

## 8. Video analysis architecture (confirmed, unchanged)

Single-stage Job #1 (`["video_analysis"]`). `VideoAnalysisWorker` is a plain `Worker` subclass — gets idempotency/retry/crash-recovery for free, same as any other worker. Its result (duration, fps, resolution, codec, orientation, brightness/contrast, loudness, silence-at-boundaries, transcript, faces, scene changes, camera motion, blur score) is copied/referenced onto the `videos` row so it survives past Job #1 and is queryable by video, not by job.

---

## 9. Worker architecture (updated — Notification Worker removed)

| Worker | Stage/topic name | Notes |
|---|---|---|
| Video Analysis Worker | `video_analysis` | Job #1, sole stage |
| Crop Worker | `crop` | ffmpeg/OpenCV crop+resize+rotate |
| Color Worker | `color` | brightness/contrast/saturation |
| Audio Worker | `audio` | normalization, silence removal |
| Subtitle Worker | `subtitle` | Whisper transcription + burn-in |
| Stabilization Worker | `stabilize` | later, not required for V1 |
| Compression Worker | `compress` | later, not required for V1 |
| Export Worker | `export` | final container/resolution/format |
| Thumbnail Worker | `thumbnail` | later, not required for V1 |

~~Notification Worker~~ — **removed for V1**. The frontend submits a job, gets a `job_id`, and polls `GET /jobs/{id}` (already implemented, already returns status/progress). This is strictly less to build, and it's honest about what V1 actually needs: nothing currently consumes a "job done" event except a UI that could just as easily ask. Revisit only if polling proves too slow/chatty in practice — see §20.

Planner is deliberately absent from this table — it's not a Kafka worker, it lives in the API process (§5).

---

## 10. Event flow (updated — no lifecycle event/notification hop in V1)

**Job #1 — Analysis:**
```
video.uploaded → [outbox] → video_analysis topic → VideoAnalysisWorker → Result
  → metadata copied onto video row
```

**Conversational planning (no Kafka):**
```
video has metadata → chat turns → planner → proposal produced → user confirms
  → proposal validated + compiled (§6) → Job #2 submitted
```

**Job #2 — Edit:**
```
[outbox] → crop → CropWorker → Result
       → color → ColorWorker → Result
       → audio → AudioWorker → Result
       → subtitle → SubtitleWorker → Result
       → export → ExportWorker → Result
```

No further hop after the last stage in V1 — the frontend already knows a poll will show `status: completed`. Whatever triggers the memory-update LLM call (§7) is a concern of the polling endpoint, not of a Kafka consumer — see §19 Phase 6 for exactly where that hook lives.

---

## 11. Job lifecycle (confirmed, unchanged)

Same state machine, same engine, used identically for both jobs. Not modified in any phase below.

---

## 12. Components that remain unchanged (confirmed, unchanged)

`JobSubmissionService`, `WorkflowEngine`, `StageProcessingService`, `WorkerRunner`, `Worker` ABC, `OutboxRepository`/`OutboxPublisher`, all existing models/repositories, observability stack, `jobs` API routes, docker-compose infra. Every phase in §19 through Phase 7 is designed so none of these need to change. From Phase 8 onward that hard rule relaxes to a strong default — prefer building above Setu, but a modification with a documented good reason is allowed (see the posture note at the top of Phase 8; Phase 9b's cooperative cancellation is the first such case).

---

## 13. Components to add (updated)

- Storage abstraction (§14, first priority — see Phase 0)
- `videos`, `conversations`, `messages`, `user_preferences` tables
- Video Analysis Worker + new editing workers (§9)
- Capability registry (code, not DB) + proposal validator/compiler (§6)
- Planner service (LLM call + the two-shape response)
- Chat endpoint(s)
- ~~Notification delivery mechanism~~ — not needed; `GET /jobs/{id}` polling covers it

---

## 14. Database additions (updated per v5 — see changelog)

```
projects(id, owner_id, name, created_at, updated_at)

videos(id, project_id FK -> projects.id (NOT NULL, CASCADE), storage_uri,
       original_filename, name (nullable, user-entered display name),
       latest_analysis_job_id FK -> jobs.id (nullable),
       created_at, updated_at)

conversations(id, project_id FK -> projects.id (NOT NULL, UNIQUE),
              created_at, updated_at)

messages(id, conversation_id FK, sender_id (nullable), role, content,
         created_at)

user_preferences(user_id PK, preferred_platform, preferred_export_format,
                  captions_enabled, preferred_resolution, subtitle_language,
                  updated_at)
```

`Project` is the aggregate root everything above hangs off (v5) — no video, conversation, or message exists independent of one. No `proposals` table before Phase 9a (a confirmed proposal before then is just what's already on the submitted `Job` row); Phase 9a adds one for the approval workflow (§19). No vector store, no generic events table.

---

## 15. New Redpanda topics (updated)

`video_analysis`, `crop`, `color`, `audio`, `subtitle`, `export` (+ `.dlq` for each, already generic, no config needed). ~~`job-lifecycle`~~ — dropped for V1 along with the Notification Worker; see §20 if it's ever needed.

---

## 16. Responsibilities recap (confirmed, unchanged)

- **Planner**: decide stages/order/params from the registry; ask clarifying questions; never execute.
- **Orchestrator** (`WorkflowEngine` + `StageProcessingService`): own workflow position and transactional dispatch; unaware this is video.
- **Workers**: one deterministic operation each; never call the LLM; never talk to each other; never decide workflow.

---

## 17. Conversation & memory flow (confirmed, with the trigger question resolved)

Conversation flow unchanged from v1: chat → planner → message-or-proposal → confirm → validate/compile (§6) → submit Job #2 → poll for completion.

Memory flow's open question from v1 — "what triggers the post-success preference-update call, without a Notification Worker?" — is resolved explicitly, not via a side effect on a GET: the frontend polls `GET /jobs/{id}` (read-only, no side effects) and, on observing `status: completed`, calls `POST /jobs/{id}/update-memory` (§19 Phase 6). That endpoint loads the completed conversation, runs the one lightweight preference-update LLM call, upserts `user_preferences` if warranted, and returns. It's idempotent, so the frontend calling it more than once (double-poll, retry, multiple tabs) is harmless. If Setu later gains a real terminal lifecycle event (§20), this same logic moves into a background consumer of that event without changing anything about the endpoint's contract or the overall architecture — only what triggers it changes.

---

## 18. Failure scenarios (confirmed, unchanged from v1)

Corrupt video / unsupported codec, planner failure, worker crash, export failure, in-flight proposal change — all handled exactly as described in v1 (retry/backoff/DLQ for anything inside Setu's Kafka path; local retry+graceful chat message for planner failures, since nothing durable has been submitted yet). Not repeated in full here; nothing about the refinements in this update changes any of those mechanics.

---

## 19. Implementation roadmap

Each phase is independently demoable and none requires reopening a prior phase's code — later phases only add new workers/tables/endpoints, never restructure what's already there.

### Phase 0 — Storage foundation

**Goal.** A place for uploaded video bytes and per-stage artifacts to live, with a worker-readable URI scheme. Nothing else in this plan can run without it.

**Why before Phase 1.** Video Analysis Worker needs a real file to read; the API needs somewhere to put an upload before it can even create a Job.

**Components.** `backend/storage/` gets a small interface (`put`, `get_uri`, `exists`) with a local-disk implementation. No S3 yet — the interface is what makes swapping that in later a config change, not a rewrite.

**Database changes.** None yet (the `videos` table lands in Phase 1, once there's something to attach storage to).

**API changes.** None yet — storage is exercised directly in tests first; the upload endpoint arrives in Phase 1 alongside it.

**Worker changes.** None.

**Frontend changes.** None.

**Testing strategy.** Unit tests against the storage interface: put a file, get it back by URI, confirm non-existent URIs error cleanly.

**Demo scenario.** A script (or test) writes a video file through the storage interface and reads it back byte-for-byte.

**Risks.** Picking a URI scheme that's awkward to later map onto S3 keys — keep it a plain opaque string from day one so nothing downstream parses its structure.

---

### Phase 1 — Upload + Video Analysis Worker (Job #1, end to end)

**Goal.** A real video, uploaded through the API, gets analyzed by a real (if initially simple) worker, and the metadata lands somewhere queryable — proving the *entire* Job #1 path end to end, through Setu's existing, unmodified machinery.

**Why before Phase 2.** The conversation layer's whole reason to exist is to plan against real metadata; there's nothing to plan against until this phase exists.

**Components.** `VideoAnalysisWorker` (new `Worker` subclass, registered in `WORKERS`); upload endpoint that writes to storage (Phase 0) and calls `JobSubmissionService.submit(workflow=["video_analysis"], payload={"video_uri": ...})` **unmodified**.

**Database changes.** `videos` table (§14), belonging to a `Project` (v5 — every video requires one; `POST /projects` creates it). `latest_analysis_job_id` populated once the worker succeeds.

**API changes.** `POST /projects/{project_id}/videos` (upload) → creates `videos` row + Job #1, returns `video_id` + `job_id` (v5 — was flat `POST /videos`). `GET /videos/{id}` stays flat. `GET /projects/{id}/videos` lists a project's videos — multiple videos per project already works, no unique constraint blocks it. `GET /jobs/{id}` already exists and needs no change to report Job #1's progress.

**Worker changes.** One new worker. Start with a subset of the metadata list (say: duration, fps, resolution, codec) if the full analysis stack (Whisper, scene detection, etc.) isn't ready yet — the point of this phase is proving the *pipeline*, not shipping every metric on day one. Add the rest incrementally without touching anything else.

**Frontend changes.** None yet (or a bare upload form if you want something clickable early).

**Testing strategy.** Integration test: upload a small test video, poll until `completed`, assert the `videos` row has metadata. Also re-run the existing kill-worker-mid-job test pattern against this new worker to confirm crash recovery holds for it too — it should, for free, but worth proving once.

**Demo scenario.** Upload a video via the API, watch Job #1 go `pending → running → completed`, see real metadata on the video record.

**Risks.** Being tempted to build the full analysis stack (Whisper + scene detection + face detection all at once) before proving the plumbing — resist; ship partial metadata first, expand later, each addition needs no other change. `videos.name` — an optional user-entered display name, distinct from `original_filename` — was added the same way this note originally predicted it could be: a nullable column and an optional form field, no restructuring required.

---

### Phase 2 — Conversation mechanics (stub planner)

**Goal.** Prove the chat loop — messages persisted, ordered, retrievable — without yet depending on a real LLM.

**Why before Phase 3/4.** Decouples "does the conversation plumbing work" from "is the LLM prompt good," so a bad prompt later doesn't also mean debugging persistence at the same time.

**Components.** `conversations`/`messages` tables and repositories; a chat endpoint; a **stub planner** that returns a fixed, hardcoded `{"type": "proposal", ...}` response (or a fixed clarifying question on the first turn) regardless of input. `user_preferences` table also created here (empty rows, sensible defaults) so the read-path wiring exists early even though nothing writes to it until Phase 6.

**Database changes.** `conversations`, `messages`, `user_preferences` (§14).

**API changes.** `POST /projects/{id}/messages` (post a chat message, get the assistant's stubbed reply back), `GET /projects/{id}/messages` (history) — project-scoped from the start (v5: `Conversation` belongs to `Project`, not `Video`), matching how this actually shipped.

**Worker changes.** None.

**Frontend changes.** None yet.

**Testing strategy.** Post several messages, confirm ordering and persistence; confirm the stub planner's fixed response is returned and appended as an assistant message.

**Demo scenario.** A short scripted back-and-forth against the API showing message history growing correctly, with a canned "proposal" coming back on cue.

**Risks.** Under-scoping "recent N messages" — pick a concrete number now (e.g. last 20) rather than leaving it open, since that number is a real prompt-budget decision (§4).

---

### Phase 3 — Capability registry + proposal validation/compilation (still no real LLM)

**Purpose (added 2026-07-27, before implementation).** This phase establishes the proposal validation and workflow compilation pipeline. It intentionally introduces no AI-specific architectural decisions — no orchestration, no memory systems, no multi-agent workflows, no advanced planning (see the AI architecture principles under §5). Its only responsibility is proving that a valid proposal can be converted into an executable Setu workflow.

**Goal.** Build the registry and the validator/compiler from §6, and prove a proposal (still hand-authored, not LLM-generated) can be turned into a real Setu Job #2 and executed — using the **existing `dummy` worker already in the codebase** as a stand-in stage, not new throwaway fake workers.

**Why before Phase 4.** Isolates "is the planner→Setu translation correct" from "is the LLM's output good" — the same reasoning as Phase 2. If Phase 4 later produces a bad job, you'll already know the compilation layer isn't the cause.

**Components (revised 2026-07-27 for extensibility, before any code was written — see conversation for full rationale on each):**
- `CapabilityRegistry` — a small class (`get`/`exists`/`list`/`register`) wrapping an internal dict, not a bare module-level constant. The validator depends on this interface, not a global, so Phase 5 can register real workers one at a time without touching validator logic. Still backed by a hardcoded dict for V1.
- `StageCapability` — `name`, `description`, `parameter_schema` (renamed from `params` — same meaning, clearer). Deliberately **not** adding `worker_name`, `category`, `version`, or `supports_batch` yet: `worker_name` would duplicate the identity §6 already establishes (`stage name = topic name = WORKERS dict key`), and the other three have no consumer anywhere in Phase 4 or Phase 5's plan as written. Added in whichever sub-phase first needs them — cheap, since this is a dataclass in code, not a migration.
- `Proposal` / `ProposalStage` — small frozen dataclasses (matching `StageMessage`'s existing convention in `workers/base.py`) replacing raw dicts inside the validation/compilation pipeline. Pydantic stays the API-schema layer only (see `api/schemas/`); these are internal domain objects, not request/response bodies, so a plain dataclass is the convention match, not Pydantic.
- Validator returns a `ValidationResult` (`valid: bool`, `errors: list[str]`) instead of only raising — and **collects every error found**, not just the first (unregistered stage *and* malformed params in the same proposal both get reported together). This matters concretely at Phase 4: the planner's regenerate-and-retry loop feeds validation errors back into the LLM prompt, and "unknown stage 'crp', unknown param 'britness' on 'color'" is a far better retry signal than one error at a time.
- `ExecutionContext` — a one-field dataclass (`video_uri: str`) passed alongside the `Proposal` to the compiler, instead of a bare `video_uri=...` keyword. Keeps the compiler's signature stable for Phase 4+, when more than a single URI will need to travel alongside a proposal.
- The compiler (`compile_workflow(proposal, context) -> (workflow, payload)`, replacing "translator") stays a **pure function** — no DB, no storage, no API calls, no job submission, no state mutation. Renamed from "translator" because its future responsibilities (asset resolution, param normalization, default injection, shorthand expansion) are closer to compilation than plain translation.
- Registry/validator/compiler are already asset-agnostic — they only ever see `stage` and `params`, never a video — so no video-specific naming needs correcting there. `ExecutionContext.video_uri` stays as-is: `Video` is the only asset-like model that actually exists in this codebase today, so naming the field `asset_uri` would be less accurate, not more, until an `Asset` concept is real.

**Scope boundary — what does NOT change in Phase 3.** `Planner`/`StaticPlanner` (`backend/services/planner.py`) and `ConversationService` still pass and store a plain `dict[str, Any]` (via `json.dumps` into `Message.content`) — Phase 3 has no wiring into the conversation flow at all, so there is nothing there to convert yet. `Proposal`/`ProposalStage` are introduced only inside the new validation/compilation pipeline. Converting the planner's output into these domain models is Phase 4's job, when the real LLM response is parsed for the first time.

**Duplicate stage names.** Rejected for V1. Setu's engine would mechanically tolerate a repeated stage (`stage_params` is index-keyed, `Result` rows are keyed by `(job_id, stage_index)`, dispatch is positional) — so this is a deliberate validator policy, not a hard constraint from Setu, and it's asserted with a test rather than left undefined. Rationale: none of V1's five editing stages (crop/color/audio/subtitle/export) has a legitimate reason to run twice in one job, and a duplicate is far more likely to be an LLM hallucination than an intentional request — easy to loosen later if a real use case shows up.

**Database changes.** None new.

**API changes.** None new. The optional debug endpoint mentioned in earlier drafts is skipped — the integration test already exercises the full pipeline (proposal → validation → compilation → `JobSubmissionService` → `WorkflowEngine` → `DummyWorker` → completed `Job`), and the real user-facing entry point arrives in Phase 4 when the planner is wired into the conversation flow.

**Worker changes.** None new — reuses the existing `dummy` worker, registered under a stand-in stage name in the registry purely to prove the wiring.

**Frontend changes.** None.

**Testing strategy.** Unit tests: a proposal with an unregistered stage is rejected; a proposal with malformed params is rejected; a proposal with duplicate stage names is rejected; a proposal with multiple simultaneous errors reports all of them via `ValidationResult`; a valid proposal compiles to the exact expected `workflow`/`payload` shape. One integration test: submit a hand-written valid proposal through the full path and see the resulting Job complete via the existing engine.

**Demo scenario.** Feed a hand-authored `{"summary": ..., "workflow": [...]}` JSON into the validator/compiler, show it becomes a real `Job` that runs to completion — with zero changes to `JobSubmissionService`/`WorkflowEngine`/`StageProcessingService`.

**Risks.** Over-building the param validator (full JSON-Schema, custom DSL, etc.) — a flat "known keys + basic type check" is enough for V1; tighten only if bad proposals actually get through in practice.

---

### Phase 4 — Real planner LLM (revised per Changelog v8)

**Goal.** Replace `StaticPlanner` with `LLMPlanner`, reusing the entire Phase 3 proposal pipeline unchanged in spirit (validator logic untouched; compiler's responsibility unchanged, only its per-stage video shape grows). One question this phase answers and nothing more: *can a real LLM generate a valid proposal that executes successfully through the existing Setu pipeline?* Built per the v6 AI architecture principles: one stateless planner call per turn, no agent framework, no memory beyond §7, no premature architecture for phases that aren't here yet.

**Why before Phase 5.** The planner needs *some* registered stages to plan against; Phase 3 already proved the plumbing against `dummy`, so swapping in the real LLM here is a contained change — only the "what produces the proposal JSON" piece moves, nothing around it does.

**Components.**
- **`PlannerContext`** — replaces "many unrelated arguments" into the planner with one dataclass: project, conversation history, the project's videos (each exposed with a short LLM-facing handle, e.g. `video_1`, not a raw UUID), user preferences, the capability registry, and an `approval_policy` field that's a future-compatible placeholder (unused until Phase 9a). Assembled by `ConversationService` before every planning call; the planner never fetches data itself.
- **`PromptBuilder`** — `PlannerContext → Prompt`, extracted as its own service so future prompt types (clarification, revision, summaries) reuse the same construction step.
- **`LLMClient` / `GroqClient`** — a thin abstraction so `LLMPlanner` depends on an interface, not the `groq` SDK directly. Only one concrete implementation ships now; other providers are not stubbed out ahead of need. Uses Groq's schema-enforced `response_format={"type": "json_schema", ...}` — not prompt-engineered JSON.
- **`LLMPlanner(Planner)`** — `respond(context: PlannerContext) -> PlannerResponse`. **This changes `Planner`'s interface signature** (was `respond(messages, preferences) -> dict`); `StaticPlanner` and its Phase 2 tests move to the new signature too, since both implement the same ABC. Both the input and the output are now typed domain objects — `PlannerResponse` (`type` / `message` / `proposal`, where `proposal` is Phase 3's existing `Proposal`) replaces the raw `dict[str, Any]` on both sides of the call, per the "avoid raw dictionaries" principle. `ConversationService` is the only caller and now serializes `PlannerResponse` into `Message.content` itself.
- **Two-layered, bounded retry.** Infrastructure retry (network failure, provider timeout, malformed/non-parseable structured output) is separate from semantic retry (Phase 3's `validate_proposal` rejects the proposal → regenerate exactly once with the `ValidationResult` errors fed back into the prompt → if still invalid, return a friendly clarification message instead of a job). No recursive retries, no reflection/self-critique loop, no unbounded regeneration.
- **`validate_proposal` is reused exactly as implemented in Phase 3** — no changes to that file in this phase (see the known Phase 5 landmine below).
- **`compile_workflow`'s video shape changes, its responsibility doesn't.** `ExecutionContext.video_uri: str` → `ExecutionContext.video_uris: dict[str, str]` (handle → resolved storage URI, built by `ConversationService`); `ProposalStage` gains `video_ids: list[str]`. Still a pure `Proposal → Workflow` function — this is the Multiple Videos decision below, not a new "asset resolution" responsibility.
- **Multiple videos are in scope for Phase 4.** A project already holds many `Video` rows; the planner now explicitly chooses among them per stage via handles, rather than every stage implicitly sharing one project-wide video. This is deliberately narrower than the `Asset`/`AssetRegistry` system deferred to Phase 5+ (see below) — just an id→uri dict over already-known `Video` rows.
- **Lightweight observability**: provider, model, latency, prompt/completion tokens, whether regeneration was used, validation success/failure. Additive logging only, no telemetry redesign.

**Database changes.** None new — confirm-proposal obtains the latest proposal by scanning conversation history, not from a new table (proposal persistence stays deferred to Phase 8/9).

**API changes.** Chat endpoint now round-trips through `LLMPlanner`. **`POST /projects/{id}/confirm-proposal`** is the sole confirmation entry point (no free-text chat confirmation): it scans the conversation backwards for the most recent `{"type": "proposal"}` message (not just the last message, since a clarifying turn can follow a proposal), compiles it, resolves video handles to URIs, and calls `JobSubmissionService.submit()`. Idempotent via Setu's existing idempotency-key mechanism (a deterministic key derived from the conversation id + proposal message id) — calling it twice replays the same `Job`, never creates a second one.

**Worker changes.** None new yet — still targets whatever's registered (§9's real workers land in Phase 5; until then, keep `dummy` registered so this phase is fully testable on its own).

**Frontend changes.** None yet, unless useful to demo manually via a REST client.

**Testing strategy.** Prompt-level tests with representative conversations (vague request → clarifying question; specific request → valid proposal referencing one or more videos; nonsense request → graceful handling); infra-retry test for a simulated LLM failure/timeout; semantic-retry test proving exactly one regeneration attempt on validation failure, then a clarification message; `confirm-proposal` idempotency test (two calls, one `Job`).

**Demo scenario.** The example from the spec, live: "Make this look more professional" → assistant asks who it's for → "LinkedIn" → assistant proposes a concrete workflow → user calls `confirm-proposal` → a real Job #2 is submitted (still against `dummy` until Phase 5).

**Risks.** LLM hallucinating a stage name, malformed params, or a video handle that doesn't exist — all caught by Phase 3's unmodified validator plus `ConversationService`'s handle resolution; if it's tripping constantly, that's a prompt/registry-description problem, not a reason to loosen validation. Separately: `validate_proposal`'s duplicate-stage-name rejection is keyed only on stage name, so a future `crop(video_1) → crop(video_2)` workflow will collide once Phase 5 registers `crop` — can't trigger yet (registry only has `dummy`), but Phase 5 will need to change the uniqueness key to `(stage name, video_ids)`.

**Deferred out of this phase (Changelog v8):** richer `StageCapability` fields, any `compile_workflow` responsibility beyond `Proposal → Workflow`, the generic `Asset`/`AssetRegistry`/`AssetResolution` system, and reshaped worker/capability metadata all wait for Phase 5; proposal persistence/versioning/revisions/approval-engine/collaborative-editing/ownership wait for Phase 8/9.

---

### Phase 5 — Real deterministic editing workers (one worker at a time)

**Goal of the phase as a whole.** Swap real Crop/Color/Audio/Subtitle/Export workers in for `dummy`, one at a time, in the order below. Each sub-phase is a complete, shippable milestone on its own — the system is fully working and demoable after every single one, not just at the end. This is the whole point of splitting it this way: if any one worker turns out to be far more complex than expected (subtitle/Whisper is the likely candidate), that complexity is contained to its own sub-phase and never blocks the others from already being done and demoed.

**Why before Phase 6.** Memory only has something worth remembering once real edits are actually happening — no point wiring "did the user like this edit" against fake output.

**Shared mechanics across all five (stated once, not repeated per worker).** Each is a new `Worker` subclass + one line in the `WORKERS` registry + one entry in the capability registry (§6) with its real param schema, replacing `dummy` for that stage name. Setu's "adding a worker touches nothing else" guarantee gets exercised five times here and should hold every time — if any sub-phase requires touching `WorkflowEngine`, `StageProcessingService`, or `JobSubmissionService`, that's a signal something's being done wrong, not a signal those components need to change.

**Database changes** (all five): none new. **API changes** (all five): none new. **Frontend changes** (all five): none yet — Phase 7 is where these become visible in a UI.

---

**Phase 5a — Crop Worker**

- **Objective.** Deterministically crop/resize/rotate a video to requested dimensions or aspect ratio.
- **Implementation tasks.** `CropWorker(Worker)` calling ffmpeg (or OpenCV) with params from the proposal (e.g. `{x, y, width, height}` or `{aspect_ratio}`); register in `WORKERS` as `"crop"`; add `crop`'s real param schema to the capability registry, replacing the `dummy` stand-in used in Phases 3–4.
- **Testing strategy.** Known input video + known crop/aspect params → assert output resolution/aspect ratio matches exactly. Malformed params (out-of-bounds crop rect) → assert a clean failure, not a corrupt output file.
- **Demo scenario.** Submit a Job #2 with `workflow: ["crop"]` and a 16:9 → 9:16 aspect-ratio request; produce a real vertically-cropped output file.
- **Acceptance criteria.** Output video has the exact requested dimensions/aspect ratio; job reaches `completed`; a deliberately bad crop rect reaches `dead_lettered` via the existing retry/DLQ path with no changes to that path.

**Phase 5b — Color Worker**

- **Objective.** Deterministically adjust brightness/contrast/saturation.
- **Implementation tasks.** `ColorWorker(Worker)`; register as `"color"`; param schema for brightness/contrast/saturation deltas or targets.
- **Testing strategy.** Known input + known adjustment → assert measurable output brightness/contrast shifts in the expected direction and rough magnitude (sampled pixel/histogram check, not exact-match, since encoding isn't lossless).
- **Demo scenario.** Submit `workflow: ["color"]` with a brightness increase; show the output is visibly and measurably brighter.
- **Acceptance criteria.** Output's measured brightness/contrast moves in the requested direction within a reasonable tolerance; job reaches `completed` on valid input.

**Phase 5c — Audio Worker**

- **Objective.** Normalize loudness and optionally remove silence, while preserving background music where requested.
- **Implementation tasks.** `AudioWorker(Worker)`; register as `"audio"`; param schema for `normalize`, `remove_silence`, `preserve_music` (or similar); ffmpeg loudnorm filter (or equivalent) for normalization, silencedetect/silenceremove for trimming.
- **Testing strategy.** Known input with known loudness → assert output loudness lands near the target LUFS; known input with a silent lead-in → assert the silent portion is removed from the output's duration.
- **Demo scenario.** Submit `workflow: ["audio"]` against a clip with a quiet intro and inconsistent volume; show the output starts immediately and is consistently loud.
- **Acceptance criteria.** Output loudness within tolerance of target; silence-trimmed output duration reflects the removed segment; job reaches `completed`.

**Phase 5d — Subtitle Worker**

- **Objective.** Generate a transcript-based caption track and burn it into the video.
- **Implementation tasks.** `SubtitleWorker(Worker)`; register as `"subtitle"`; param schema for language/style; reuse the transcript from Video Analysis (§8) if already present rather than re-running Whisper, falling back to running it here if analysis didn't include one. Burn-in via ffmpeg's subtitle filter.
- **Testing strategy.** Known input with known speech → assert generated captions roughly match expected text (spot-check, not exact-match given ASR variance); assert burned-in captions are actually present in the output frames (e.g. via a frame sample check) rather than silently skipped.
- **Demo scenario.** Submit `workflow: ["subtitle"]` against a spoken-word clip; show the output with visible burned-in captions matching the speech.
- **Acceptance criteria.** Output has visible captions synced to speech within a reasonable tolerance; job reaches `completed`; this is flagged going in as the most likely sub-phase to run long (ASR is the least deterministic step in the whole pipeline) — if it does, that risk is contained here and doesn't block 5a–5c, which are already done and demoed.

**Phase 5e — Export Worker**

- **Objective.** Produce the final container/resolution/format/bitrate as the last stage of any edit workflow.
- **Implementation tasks.** `ExportWorker(Worker)`; register as `"export"`; param schema for target resolution/format/bitrate; writes final output through the storage abstraction (§14) so it's retrievable by the frontend.
- **Testing strategy.** Known input → assert output container/resolution/format matches requested target and is playable (basic ffprobe sanity check on the result).
- **Demo scenario.** Chain all five: `workflow: ["crop", "color", "audio", "subtitle", "export"]` — the full LinkedIn example from Phase 4's demo, now producing genuinely edited, exported output end to end.
- **Acceptance criteria.** Final artifact is a valid, playable video in the requested format; a full multi-stage workflow (all five workers) completes successfully and each intermediate `Result` (from Crop through Subtitle) is still present and correct in Postgres, proving the chain, not just the last stage, actually ran.

---

### Phase 6 — Memory / preference loop

**Goal.** Close the loop described in §7/§17: preferences are actually read before planning (already true since Phase 2/4) and actually written after a successful real edit — via an explicit action, not a side effect hidden inside a read.

**Why before Phase 7.** Frontend integration is more meaningful once there's an observable "the assistant remembered my platform preference" behavior to show, not just a working pipeline.

**Components.** A new endpoint, `POST /jobs/{id}/update-memory`, called explicitly by the frontend once it observes (via its normal `GET /jobs/{id}` poll) that the job has reached `completed`. The endpoint:
1. Loads the completed job's conversation.
2. Runs the one lightweight LLM call asking whether any durable preference should be saved.
3. Upserts `user_preferences` if the call says so.
4. Returns immediately once done.

`GET /jobs/{id}` itself is untouched — still purely read-only, no hidden side effects. This is a deliberate, explicit design choice: the client decides when to trigger memory processing, rather than it being an incidental consequence of polling.

**Database changes.** A flag to make the endpoint idempotent — e.g. `conversations.memory_processed_at` (nullable timestamp). On a call, if it's already set, the endpoint returns immediately without re-running the LLM call or re-upserting preferences; if not, it does the work and sets it. Calling the endpoint five times in a row is safe and cheap after the first.

**API changes.** New `POST /jobs/{id}/update-memory` — synchronous, idempotent, no request body needed (or an empty one). Returns success once the check/update is done (or immediately, no-op, if already processed). `GET /jobs/{id}` unchanged.

**Worker changes.** None.

**Frontend changes.** After a poll shows `status: completed`, the frontend calls this endpoint once (and it's safe if called more than once — double-poll, retry, multiple tabs, etc. all land on the same idempotent outcome).

**Testing strategy.** Complete a job; call the endpoint, assert `user_preferences` reflects a plausible update from a scripted conversation and `memory_processed_at` is now set; call it again, assert no second LLM call happens and the response still succeeds (idempotency).

**Demo scenario.** Tell the assistant "always export for LinkedIn" during a session, complete an edit, call `update-memory` explicitly, start a *new* conversation about a *different* video, and show the planner already assumes LinkedIn without being told again.

**Risks.** None specific to this design beyond the general LLM-call risk already covered in Phase 4 (handled the same way: local retry/backoff, and if it ultimately fails, the endpoint should still return success for the *job* itself — a missed preference update is a soft failure, not a reason to surface an error to the user). If Setu later gains a real terminal lifecycle event (`job.completed`, §20), this endpoint's *logic* moves unchanged into a background consumer of that event — only the trigger changes, not the contract.

---

### Phase 7 — Frontend integration

**Goal.** A usable UI over everything built so far: upload, chat, proposal confirmation, poll-based progress, completed video download.

**Why last.** Every piece it depends on (upload, chat, planning, execution, memory) is already independently proven by this point — this phase is wiring, not new backend logic.

**Components.** Project creation + upload widget → `POST /projects`, `POST /projects/{id}/videos`; chat UI → the messages endpoints; a confirm affordance for proposed workflows; a progress view driven by polling `GET /jobs/{id}` (a simple interval poll is enough for V1, per your instruction — no SSE/WebSocket needed), which on observing `completed` calls `POST /jobs/{id}/update-memory` (Phase 6) once.

**Database changes.** None.

**API changes.** None, unless a small "list my videos/conversations" convenience endpoint is worth adding for navigation — optional, not required by anything above.

**Worker changes.** None.

**Frontend changes.** The whole phase.

**Testing strategy.** Manual end-to-end walkthrough of the full spec example (upload → "make this more professional" → LinkedIn → confirm → watch progress → download); basic component tests for the chat/upload widgets if the frontend stack has a test setup already.

**Demo scenario.** The full product demo: a real video, a real conversation, a real confirmed proposal, real progress, a real output file.

**Risks.** Polling interval too aggressive (hammering the API) or too slow (feels unresponsive) — pick something reasonable (2–3s) and revisit only if it's actually a problem.

---

### Phase 8 — Collaborative Project Rooms

**Goal.** Extend the application from a single-user experience into a shared workspace: multiple users join an existing **Project** — already created back in Phase 1, already holding its videos and shared conversation (Phase 2) — and see the same videos, shared conversation, job progress, and completed exports. This phase is collaboration *infrastructure* only, and specifically just **membership**: `Project` is already the aggregate root (v5) and `Conversation`/`Video` are already project-scoped, not video- or user-scoped. What's missing is *who else* is allowed in. The planner behaves exactly as it does today (it simply receives the shared conversation as context, per §4), and no approval workflow or AI consensus logic exists yet; that's Phase 9a.

**Why after Phase 7.** Everything a room member observes — upload, chat, planning, progress, download — already works single-user by the end of Phase 7. This phase multiplexes an existing experience; it doesn't build a new one.

**Posture note (applies from here on).** Phases 0–7 treated "no changes to Setu's core" as a hard rule. From Phase 8 onward it is a *default*: prefer building above Setu, but modify it when there's a genuinely good reason, and document that reason at the point of change. Phase 8 and Phase 9a need no such change; Phase 9b makes the one exception (cancellation).

**Core concept.** A Project already groups its videos and one shared conversation (Phase 1/2, v5). This phase adds the missing piece: members, turning a single-owner project into a genuine multi-user room. Every message already preserves sender (`Message.sender_id`, nullable, null = assistant), timestamp, and ordering — Phase 2 built that in from the start specifically so this phase wouldn't need to touch it.

**Components.** `project_members` model + repository (the only new table — `projects`, `conversations`, `videos` all already exist and are already project-scoped); a room-snapshot endpoint; a WebSocket fanout layer (designed below, implemented as thin as possible).

**Database changes.** One new table:

```
project_members(project_id FK, user_id, role ('owner'|'member'), joined_at,
                PRIMARY KEY (project_id, user_id))
```

`POST /projects` (Phase 1) needs one small addition here: also insert the owner as the first `project_members` row, so a freshly-created project is never memberless. No changes needed to `conversations`, `messages`, or `videos` — all three already carry the `NOT NULL` `project_id`/sender attribution this phase needs (v5).

- **`jobs` stays untouched.** Room↔job linkage lives in a small `project_jobs(project_id, job_id)` mapping table written at submission time by the (non-Setu) submission wrapper. If that join ever proves genuinely annoying, a nullable `project_id` column on `jobs` is the fallback — an acceptable Setu change under the new posture, but not the starting point.
- **Version history is derived, not stored:** the room's completed export jobs (via `project_jobs` → results/artifacts), ordered by completion time, *are* the version list. No new table until deriving it proves insufficient.

**API changes.**
- `POST /projects/{id}/members` — invite a member (owner-only for V1).
- `POST /projects/{id}/join` — accept an invite / join the room.
- `GET /projects/{id}/members` — list members.
- `GET /projects/{id}` — room snapshot: videos, recent messages, active jobs, completed exports. This is also the reconnect/recovery path for the socket below.
- `POST /projects/{id}/videos`, `POST /projects/{id}/messages` / `GET /projects/{id}/messages` **already exist** (Phase 1/2) and need no change — they gain multi-user meaning simply because `project_members` now exists to check against.

All room endpoints enforce membership; non-members get 403/404.

**Real-time synchronization (WebSocket architecture — designed here; implementation stays deliberately thin).**
- One socket per client per room: `WS /projects/{id}/ws` (membership-checked at connect).
- An in-process connection registry keyed by `project_id`. A single API process is the V1 reality; the scale-out path is Redis pub/sub between API processes — an extension of this design, not a redesign.
- Event envelope: `{seq, type, data}` with a per-room monotonic sequence number. Event types: `message.created`, `planner.replied`, `job.updated` (status/stage progress), `export.completed`, `member.joined`.
- **The socket is pure fanout.** Writes never travel over it — REST remains the only write path. Reconnect = refetch the snapshot via `GET /projects/{id}`, then resume the stream; the `seq` lets a client detect gaps.
- Message/member/planner events are emitted directly by the API at write time. Job-progress events need a bridge out of Setu: **V1 is a server-side watcher that polls the jobs table for the room's active jobs and pushes diffs** — the same polling decision already made in §9/§19 Phase 7, just moved server-side so N clients don't each poll. The clean upgrade is backlog item 1 (terminal lifecycle events via the outbox) feeding this same fanout — when that lands, only the producer changes; the envelope and event types don't.

**Worker changes.** None. Execution is exactly as before: every confirmed proposal still becomes a normal Setu job. `JobSubmissionService`, `WorkflowEngine`, `WorkerRunner`, `StageProcessingService`, workers, retry, DLQ, and outbox are all untouched.

**Frontend changes.** Room create/join flow; shared chat with sender names; member list; progress views driven by the room socket instead of per-client polling.

**Testing strategy.** Two simulated users post interleaved messages → both see the identical ordered history, and each message lands on both sockets exactly once. A planner reply is stored once and fanned to all members. A job submitted in the room produces the same `job.updated` sequence on every connected client. A non-member is rejected from every room endpoint and from the socket. Reconnect mid-job → snapshot + resumed stream misses nothing.

**Demo scenario.** Two browsers, one room: user A uploads a video and chats with the planner; user B watches the messages, planner replies, and job progress appear live without refreshing, then downloads the completed export.

**Risks.** The WebSocket layer growing write paths — resist; it stays fanout-only, with the DB as the single source of truth for ordering. Membership checks scattered ad hoc across endpoints — centralize in one dependency/guard from day one.

---

### Phase 9a — Proposals & Approval Workflow

**Goal.** Upgrade the planner from a single-user assistant into a collaborative facilitator that understands a discussion between multiple participants — who said what, when, where they conflict, where they agree — and, instead of a directly-confirmable plan, produces a **Proposal** that must satisfy the room's approval policy before it is compiled (Phase 3 machinery, unchanged) and submitted to Setu. Execution still happens through Setu exactly as before; only the planning layer changes, and the collaboration layer sits entirely above the execution engine. **No Setu-core changes in this sub-phase** — that's isolated in 9b.

**Why after Phase 8.** Needs the sender-attributed shared conversation and the room fanout to broadcast proposals and approvals.

**Planner changes (the only "AI" change).** The prompt now renders an attributed transcript — `Alice (10:41): "Crop vertically."` / `Bob (10:42): "Keep landscape."` — instead of anonymous user turns. The response discriminator's shape is already `{"type": "proposal", ...}` from Phase 2 (§5/§6) — nothing to rename here. What's new in this sub-phase is two additional fields on that same shape: `{"type": "proposal", "summary": ..., "workflow": [...], "reasoning": ..., "discussion_summary": ...}`; `{"type": "message"}` stays as-is and is how the planner facilitates. Facilitation behaviors — detect conflicting requests, detect agreement, summarize the discussion, explain *why* it chose this workflow (the `reasoning` field), and ask for clarification when needed — are **prompt-level capabilities, not code**. In the Alice/Bob example above, the planner must recognize the conflict and return a clarifying `message` to the team, not a proposal. The Phase 3 validator still gates every proposal's `workflow` exactly as before — nothing about multi-user input loosens validation.

The prompt also renders the room's **active approval policy** as context, and is expected to phrase its facilitation messages accordingly — e.g. "The proposal is ready for admin approval." under `admin`, vs "The proposal is ready. Waiting for approval from all team members." under `team`. This is wording only: the planner never decides *whether* a proposal is approved (that's the collaboration layer, below) — it only speaks about the policy that already governs the room. The planner **never executes a workflow directly**; it only ever produces proposals (or messages). The collaboration layer alone is responsible for collecting approvals and submitting a proposal to Setu once the room's approval policy is satisfied.

**Proposal lifecycle.** `pending → approved → submitted` (with the resulting `job_id` recorded), or `pending → rejected`. A proposal stays `pending` until the room's approval policy is satisfied. **Rejection under `team`** is decided the moment any single member rejects — since every active member must approve, one rejection makes unanimity impossible, so the proposal moves straight to `rejected` without waiting for the rest of the room to vote (`is_rejected(policy, member_count, approvals)`, the mirror of `is_satisfied`, below). A `rejected` proposal does **not** block the room: the conversation stays open, the planner sees the rejection in the transcript, and — if the discussion converges on something new — produces a fresh `proposal`-type response, which is a new `proposals` row (proposals are never mutated after rejection; the old row stays as an audit record of what was rejected and why). Approval collection and policy evaluation are plain application logic — no LLM involvement past proposal generation.

**Approval policies.** A policy is an enum on the project plus one pure function `is_satisfied(policy, member_count, approvals)` (and its mirror `is_rejected`, above) — **not** a table, so additional policies are new branches of the same two functions, never a schema change. V1 ships two:

- **`admin`** ("Admin Mode") — one designated approver's decision is final; other members' votes, if any, don't count toward the outcome. Suits a lead-driven workflow where one person signs off.
- **`team`** ("Team Mode", **the V1 default**) — every *active* project member must approve before execution begins; any single rejection ends the proposal (see lifecycle, above). Suits small creative teams making decisions collectively — the framing this default is chosen for.

Note this `admin` policy value is deliberately distinct from **job ownership** (Phase 9b) — a proposal's admin-approver need not be the same person as the job's owner; conflating the two names was a V1-draft mistake, fixed here before either ships.

**Future policies (backlog, not V1).** Majority Approval, Selected Reviewers, Two-Level Approval (Lead + Manager), Custom Approval Rules — see backlog item 5 in §20. `admin`/`team` need only the existing `proposal_approvals` vote rows; Selected Reviewers and Two-Level Approval will likely need to know *which* members are eligible to vote at all, which `admin`/`team` don't — that's new data (a reviewer set or role), not just a new enum branch, so it's deferred rather than stubbed in now.

**Database changes.** Lightweight:

```
proposals(id, project_id FK, created_by_user_id, summary, reasoning,
          discussion_summary, workflow_json, status
          ('pending'|'approved'|'rejected'|'submitted'),
          job_id FK nullable, created_at, updated_at)

proposal_approvals(proposal_id FK, user_id, decision ('approve'|'reject'),
                   created_at, PRIMARY KEY (proposal_id, user_id))

projects.approval_policy ('admin'|'team') NOT NULL DEFAULT 'team'
```

One vote row per member per proposal; a member may change their vote while the proposal is still `pending`.

**API changes.**
- Proposal *creation* is the planner's job: a `proposal`-shaped planner response is persisted as a `proposals` row and fanned out. No manual-creation endpoint in V1.
- `GET /projects/{id}/proposals` — list (filterable by status).
- `GET /proposals/{id}` — full proposal including current approval status/progress (who has voted, what the policy requires).
- `POST /proposals/{id}/approve` / `POST /proposals/{id}/reject` — member-only. Each approval triggers policy evaluation **inside one transaction with a status guard** (only one `pending → submitted` transition can ever win, and only one `pending → rejected` transition can ever win), then: if satisfied, validate + compile via Phase 3 unchanged → `JobSubmissionService.submit()` → record `job_id`, mark `submitted`; if a `team` proposal just became impossible to satisfy, mark `rejected`.
- Phase 8's socket gains `proposal.created` and `proposal.updated` event types — same envelope.

**Worker changes.** None.

**Frontend changes.** Proposal card (summary, reasoning, discussion summary, workflow steps, vote buttons, approval progress including who under `team` has yet to vote); rejected proposals shown inline in the room history, not hidden.

**Testing strategy.** Prompt-level: a conflicting transcript (Alice vs Bob above) → clarifying message, no proposal; an agreeing transcript → proposal with sane `reasoning`/`discussion_summary`; a policy-aware facilitation message matches the room's active policy. Unit: `is_satisfied`/`is_rejected` across `admin`/`team` including edge counts (single member, member leaves mid-vote). Integration: `team` approvals below unanimity → nothing submitted; last member approves → exactly one Setu job submitted even under simultaneous approvals (the transaction/status-guard race test); any single `team` reject → proposal `rejected` immediately without waiting on remaining votes, room conversation stays open, a later planner turn produces a new proposal row.

**Demo scenario.** Three-user room, `team` policy (the default). Alice: "Crop vertically." Bob: "Keep landscape." The planner names the conflict and asks the team to decide. They settle on 9:16; the planner produces a proposal with reasoning and a discussion summary, and tells the room it's "waiting for approval from all team members." Bob initially rejects it wanting a different crop; the proposal is marked `rejected` immediately, discussion continues, and the planner issues a revised proposal once the room agrees. All three members approve the revised proposal → a real Setu job submits and runs while the room keeps chatting about the next edit.

**Risks.** Consensus logic creeping into code — conflict/agreement detection lives in the prompt; code only enforces policy arithmetic. Double-submit on racing approvals — covered by the transactional status guard, and tested. Facilitation quality is prompt iteration, not schema iteration — resist adding tables to fix a prompt problem. `team` policy's reject-and-regenerate loop could churn on a room that can't agree — acceptable for V1 (it mirrors real disagreement), not something to solve with code.

---

### Phase 9b — Cooperative Cancellation

**Goal.** Let the job owner stop a running job between stages. The only sub-phase in the entire roadmap that touches Setu's core, isolated here on purpose so the one exception is reviewable, testable, and demoable on its own — independent of whether 9a's approval mechanics are even in place.

**Why after 9a.** Needs a submitted job with a recorded owner (proposal creator, from 9a) to authorize `POST /jobs/{id}/cancel` against. Could in principle ship against any job with a known owner; sequenced after 9a because that's where job ownership first gets recorded.

**Task ownership + cancellation.** When execution begins, the proposal's creator becomes the **job owner** — unrelated to the `admin` approval policy from 9a; only the job owner can cancel the running job. Other members keep discussing future edits while the job runs — the room conversation is never blocked by execution. Ownership is queryable from `proposals` (creator + `job_id`) without touching the `jobs` schema. Cancellation is **this sub-phase's one justified Setu change** (backlog item 3, pulled forward): `JobStatus.CANCELLED` exists but is unreachable today. Minimal mechanic — *cooperative cancellation*: `POST /jobs/{id}/cancel` (authorized against the job owner) sets the job's status to `cancelled`; `WorkflowEngine` checks the job's status before dispatching each next stage and stops if cancelled. The in-flight stage runs to completion — no mid-stage kills, no worker changes, no retry/DLQ changes. Reason documented per the posture note: cancellation is inherently engine-state, and faking it above Setu (ignoring results of a job that keeps running) would be dishonest state.

**Database changes.** None beyond what 9a already added — `JobStatus.CANCELLED` is an existing enum value, just newly reachable.

**API changes.** `POST /jobs/{id}/cancel` — job-owner-only.

**Worker changes.** None.

**Frontend changes.** Cancel affordance visible only to the job owner, on the running job's progress view.

**Testing strategy.** Non-owner cancel → 403. Owner cancel mid-workflow → job ends `cancelled`, no further stages dispatched, in-flight stage's result intact. Owner cancel after the job already reached a terminal state → no-op or 409, not a crash.

**Demo scenario.** Continuing from 9a's demo: Alice, as the submitted job's owner, cancels it mid-run; the in-flight stage finishes, no further stage dispatches, and the job shows `cancelled` while the rest of the room keeps working.

**Risks.** Scope creep into hard cancellation (mid-stage kill) — explicitly out of scope; cooperative-only. Engine-state honesty — this is why cancellation lives in `WorkflowEngine` itself rather than being faked one layer up.

---

## 20. Backlog — deferred, not required for V1

Kept separate deliberately, per "avoid over-engineering" — these are real, reasoned improvements, but none are load-bearing for the phases above, so none should be pulled forward without a concrete reason:

1. **Terminal lifecycle event** (`job.completed`/`job.dead_lettered` via the outbox, as in v1 §21) + a real Notification Worker/SSE/webhook — worth it only if polling in Phase 7 turns out to be genuinely too slow or chatty in practice. Still a generically useful Setu improvement whenever it does happen (any future project on this engine would benefit from "job is done" being a pushable event, not just a pollable field) — just not a V1 blocker now that polling is the explicit V1 choice. Phase 8's WebSocket job-progress bridge also starts as a server-side poll of the jobs table; when this event lands, it replaces that poller as the producer feeding the same room fanout — envelope and event types unchanged.
2. **Formalizing the capability registry as a reusable Setu-level concept** (not video-specific) — the registry built in Phase 3 is currently local to this project; promoting it into Setu proper would let any future workflow (human-authored or another LLM planner) discover valid stages the same way. Reasonable later, not needed to ship this.
3. **Cancellation** — **no longer backlog: pulled forward into Phase 9b** (job-owner-only cooperative cancellation between stages, as that sub-phase's one justified Setu change). Kept in this list only so the numbering and the original reasoning remain traceable.
4. **Stabilization / Compression / Thumbnail workers** — same mechanism as Phase 5's workers, just not needed for the core "make this more professional for LinkedIn" flow; add exactly the same way (one class, one registry line) whenever they're wanted.
5. **Additional approval policies** — Majority Approval, Selected Reviewers, Two-Level Approval (Lead + Manager), Custom Approval Rules, beyond Phase 9a's `admin`/`team`. The architecture already supports this cheaply: policies are an enum + a pure `is_satisfied`/`is_rejected` function pair, so each new policy is a new branch, not a schema change or an execution-engine change. Selected Reviewers and Two-Level Approval are the ones that will eventually need real new data (which members are eligible reviewers, or a two-tier role split) rather than just vote-counting logic — worth designing properly when one of these becomes a real requirement, not speculatively now.

None of these require restructuring anything built in Phases 0–7 — that's the point of listing them here instead of folding them in early.

---

This is architecture and planning only — no code was written or modified, and no implementation was done.
