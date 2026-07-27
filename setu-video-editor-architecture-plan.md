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

Because the planner sometimes needs to keep asking questions before it has enough information to propose anything ("Who is this video for?"), its response needs one more bit of structure than the plan schema alone: a top-level discriminator.

```json
{ "type": "message", "text": "Who is this video intended for?" }
```
or
```json
{ "type": "plan", "summary": "...", "workflow": [ ... ] }
```

The `"plan"` case's body is exactly the schema you specified — see §6. This discriminator is the one addition beyond what you wrote; flagging it explicitly rather than silently introducing it, since it's necessary for the "assistant asks clarifying questions before proposing anything" behavior in the spec.

---

## 6. Planner output schema (new — formalized per your request)

Canonical shape for the `"plan"` case:

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
workflow = [item["stage"] for item in plan["workflow"]]          # -> Job.workflow
stage_params = {str(i): item["params"] for i, item in enumerate(plan["workflow"])}
payload = {"video_uri": ..., "stage_params": stage_params}       # -> Job.payload
```

This is the same "index-keyed stage params live inside the existing freeform `payload`" idea from v1, just now explicitly framed as a translation step with the planner's schema as its input — the planner never has to know or care that Setu's `Job.workflow` is a flat string list. **This translation function is the one new piece of code between "planner" and "Setu."** Everything on the Setu side of it (`JobSubmissionService.submit(workflow=..., payload=...)`) is called completely unmodified.

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
video has metadata → chat turns → planner → plan proposed → user confirms
  → plan validated + translated (§6) → Job #2 submitted
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
- Capability registry (code, not DB) + plan validator/translator (§6)
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

`Project` is the aggregate root everything above hangs off (v5) — no video, conversation, or message exists independent of one. No `plans` table (a confirmed plan is just what's already on the submitted `Job` row), no vector store, no generic events table.

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

Conversation flow unchanged from v1: chat → planner → message-or-plan → confirm → validate/translate (§6) → submit Job #2 → poll for completion.

Memory flow's open question from v1 — "what triggers the post-success preference-update call, without a Notification Worker?" — is resolved explicitly, not via a side effect on a GET: the frontend polls `GET /jobs/{id}` (read-only, no side effects) and, on observing `status: completed`, calls `POST /jobs/{id}/update-memory` (§19 Phase 6). That endpoint loads the completed conversation, runs the one lightweight preference-update LLM call, upserts `user_preferences` if warranted, and returns. It's idempotent, so the frontend calling it more than once (double-poll, retry, multiple tabs) is harmless. If Setu later gains a real terminal lifecycle event (§20), this same logic moves into a background consumer of that event without changing anything about the endpoint's contract or the overall architecture — only what triggers it changes.

---

## 18. Failure scenarios (confirmed, unchanged from v1)

Corrupt video / unsupported codec, planner failure, worker crash, export failure, in-flight plan change — all handled exactly as described in v1 (retry/backoff/DLQ for anything inside Setu's Kafka path; local retry+graceful chat message for planner failures, since nothing durable has been submitted yet). Not repeated in full here; nothing about the refinements in this update changes any of those mechanics.

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

**Components.** `conversations`/`messages` tables and repositories; a chat endpoint; a **stub planner** that returns a fixed, hardcoded `{"type": "plan", ...}" response (or a fixed clarifying question on the first turn) regardless of input. `user_preferences` table also created here (empty rows, sensible defaults) so the read-path wiring exists early even though nothing writes to it until Phase 6.

**Database changes.** `conversations`, `messages`, `user_preferences` (§14).

**API changes.** `POST /projects/{id}/messages` (post a chat message, get the assistant's stubbed reply back), `GET /projects/{id}/messages` (history) — project-scoped from the start (v5: `Conversation` belongs to `Project`, not `Video`), matching how this actually shipped.

**Worker changes.** None.

**Frontend changes.** None yet.

**Testing strategy.** Post several messages, confirm ordering and persistence; confirm the stub planner's fixed response is returned and appended as an assistant message.

**Demo scenario.** A short scripted back-and-forth against the API showing message history growing correctly, with a canned "plan" coming back on cue.

**Risks.** Under-scoping "recent N messages" — pick a concrete number now (e.g. last 20) rather than leaving it open, since that number is a real prompt-budget decision (§4).

---

### Phase 3 — Capability registry + plan validation/translation (still no real LLM)

**Goal.** Build the registry and the validator/translator from §6, and prove a plan (still hand-authored, not LLM-generated) can be turned into a real Setu Job #2 and executed — using the **existing `dummy` worker already in the codebase** as a stand-in stage, not new throwaway fake workers.

**Why before Phase 4.** Isolates "is the planner→Setu translation correct" from "is the LLM's output good" — the same reasoning as Phase 2. If Phase 4 later produces a bad job, you'll already know the translation layer isn't the cause.

**Components.** Capability registry (code); validator (schema/param checks from §6); translator (`plan → workflow/payload`).

**Database changes.** None new.

**API changes.** None new — exercised via a direct call/test, or a debug endpoint that accepts a hand-written plan JSON and submits it.

**Worker changes.** None new — reuses the existing `dummy` worker, registered under a stand-in stage name in the registry purely to prove the wiring.

**Frontend changes.** None.

**Testing strategy.** Unit tests: a plan with an unregistered stage is rejected; a plan with malformed params is rejected; a valid plan translates to the exact expected `workflow`/`payload` shape. One integration test: submit a hand-written valid plan through the full path and see the resulting Job complete via the existing engine.

**Demo scenario.** Feed a hand-authored `{"summary": ..., "workflow": [...]}` JSON into the validator/translator, show it becomes a real `Job` that runs to completion — with zero changes to `JobSubmissionService`/`WorkflowEngine`/`StageProcessingService`.

**Risks.** Over-building the param validator (full JSON-Schema, custom DSL, etc.) — a flat "known keys + basic type check" is enough for V1; tighten only if bad plans actually get through in practice.

---

### Phase 4 — Real planner LLM

**Goal.** Replace the stub from Phase 2 with a real LLM call, wired to the registry and validator from Phase 3, producing genuine clarifying questions and genuine plans from real conversation + real video metadata.

**Why before Phase 5.** The planner needs *some* registered stages to plan against; Phase 3 already proved the plumbing against `dummy`, so swapping in the real LLM here is a contained change — only the "what produces the plan JSON" piece moves, nothing around it does.

**Components.** Planner service (prompt construction from conversation + preferences + metadata + registry; call the LLM; parse the `{"type": ...}` response); wiring the chat endpoint to use it instead of the stub; wiring "user confirms" to call the Phase-3 validator/translator and then `JobSubmissionService.submit()` for real.

**Database changes.** None new.

**API changes.** Chat endpoint now round-trips through the real planner instead of the stub. A confirm step (either a recognized affirmative message, or an explicit `POST /projects/{id}/confirm-plan`) triggers Job #2 submission.

**Worker changes.** None new yet — still targets whatever's registered (§9's real workers land in Phase 5; until then, keep `dummy` registered so this phase is fully testable on its own).

**Frontend changes.** None yet, unless useful to demo manually via a REST client.

**Testing strategy.** Prompt-level tests with a handful of representative conversations (vague request → clarifying question; specific request → valid plan; nonsense request → graceful handling); retry/backoff test for a simulated LLM failure/timeout.

**Demo scenario.** The example from the spec, live: "Make this look more professional" → assistant asks who it's for → "LinkedIn" → assistant proposes a concrete plan → user confirms → a real Job #2 is submitted (still against `dummy` until Phase 5).

**Risks.** LLM hallucinating a stage name or malformed params — this is exactly what Phase 3's validator exists to catch; if it's tripping constantly, that's a prompt problem to fix in the registry's descriptions, not a reason to loosen validation.

---

### Phase 5 — Real deterministic editing workers (one worker at a time)

**Goal of the phase as a whole.** Swap real Crop/Color/Audio/Subtitle/Export workers in for `dummy`, one at a time, in the order below. Each sub-phase is a complete, shippable milestone on its own — the system is fully working and demoable after every single one, not just at the end. This is the whole point of splitting it this way: if any one worker turns out to be far more complex than expected (subtitle/Whisper is the likely candidate), that complexity is contained to its own sub-phase and never blocks the others from already being done and demoed.

**Why before Phase 6.** Memory only has something worth remembering once real edits are actually happening — no point wiring "did the user like this edit" against fake output.

**Shared mechanics across all five (stated once, not repeated per worker).** Each is a new `Worker` subclass + one line in the `WORKERS` registry + one entry in the capability registry (§6) with its real param schema, replacing `dummy` for that stage name. Setu's "adding a worker touches nothing else" guarantee gets exercised five times here and should hold every time — if any sub-phase requires touching `WorkflowEngine`, `StageProcessingService`, or `JobSubmissionService`, that's a signal something's being done wrong, not a signal those components need to change.

**Database changes** (all five): none new. **API changes** (all five): none new. **Frontend changes** (all five): none yet — Phase 7 is where these become visible in a UI.

---

**Phase 5a — Crop Worker**

- **Objective.** Deterministically crop/resize/rotate a video to requested dimensions or aspect ratio.
- **Implementation tasks.** `CropWorker(Worker)` calling ffmpeg (or OpenCV) with params from the plan (e.g. `{x, y, width, height}` or `{aspect_ratio}`); register in `WORKERS` as `"crop"`; add `crop`'s real param schema to the capability registry, replacing the `dummy` stand-in used in Phases 3–4.
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

**Goal.** A usable UI over everything built so far: upload, chat, plan confirmation, poll-based progress, completed video download.

**Why last.** Every piece it depends on (upload, chat, planning, execution, memory) is already independently proven by this point — this phase is wiring, not new backend logic.

**Components.** Project creation + upload widget → `POST /projects`, `POST /projects/{id}/videos`; chat UI → the messages endpoints; a confirm affordance for proposed plans; a progress view driven by polling `GET /jobs/{id}` (a simple interval poll is enough for V1, per your instruction — no SSE/WebSocket needed), which on observing `completed` calls `POST /jobs/{id}/update-memory` (Phase 6) once.

**Database changes.** None.

**API changes.** None, unless a small "list my videos/conversations" convenience endpoint is worth adding for navigation — optional, not required by anything above.

**Worker changes.** None.

**Frontend changes.** The whole phase.

**Testing strategy.** Manual end-to-end walkthrough of the full spec example (upload → "make this more professional" → LinkedIn → confirm → watch progress → download); basic component tests for the chat/upload widgets if the frontend stack has a test setup already.

**Demo scenario.** The full product demo: a real video, a real conversation, a real confirmed plan, real progress, a real output file.

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

**Worker changes.** None. Execution is exactly as before: every confirmed plan still becomes a normal Setu job. `JobSubmissionService`, `WorkflowEngine`, `WorkerRunner`, `StageProcessingService`, workers, retry, DLQ, and outbox are all untouched.

**Frontend changes.** Room create/join flow; shared chat with sender names; member list; progress views driven by the room socket instead of per-client polling.

**Testing strategy.** Two simulated users post interleaved messages → both see the identical ordered history, and each message lands on both sockets exactly once. A planner reply is stored once and fanned to all members. A job submitted in the room produces the same `job.updated` sequence on every connected client. A non-member is rejected from every room endpoint and from the socket. Reconnect mid-job → snapshot + resumed stream misses nothing.

**Demo scenario.** Two browsers, one room: user A uploads a video and chats with the planner; user B watches the messages, planner replies, and job progress appear live without refreshing, then downloads the completed export.

**Risks.** The WebSocket layer growing write paths — resist; it stays fanout-only, with the DB as the single source of truth for ordering. Membership checks scattered ad hoc across endpoints — centralize in one dependency/guard from day one.

---

### Phase 9a — Proposals & Approval Workflow

**Goal.** Upgrade the planner from a single-user assistant into a collaborative facilitator that understands a discussion between multiple participants — who said what, when, where they conflict, where they agree — and, instead of a directly-confirmable plan, produces a **Proposal** that must satisfy the room's approval policy before it is translated (Phase 3 machinery, unchanged) and submitted to Setu. Execution still happens through Setu exactly as before; only the planning layer changes, and the collaboration layer sits entirely above the execution engine. **No Setu-core changes in this sub-phase** — that's isolated in 9b.

**Why after Phase 8.** Needs the sender-attributed shared conversation and the room fanout to broadcast proposals and approvals.

**Planner changes (the only "AI" change).** The prompt now renders an attributed transcript — `Alice (10:41): "Crop vertically."` / `Bob (10:42): "Keep landscape."` — instead of anonymous user turns. The response discriminator from §5 evolves: in a room context, `{"type": "plan", ...}` becomes `{"type": "proposal", "summary": ..., "workflow": [...], "reasoning": ..., "discussion_summary": ...}`; `{"type": "message"}` stays as-is and is how the planner facilitates. Facilitation behaviors — detect conflicting requests, detect agreement, summarize the discussion, explain *why* it chose this workflow (the `reasoning` field), and ask for clarification when needed — are **prompt-level capabilities, not code**. In the Alice/Bob example above, the planner must recognize the conflict and return a clarifying `message` to the team, not a proposal. The Phase 3 validator still gates every proposal's `workflow` exactly as before — nothing about multi-user input loosens validation.

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
- `POST /proposals/{id}/approve` / `POST /proposals/{id}/reject` — member-only. Each approval triggers policy evaluation **inside one transaction with a status guard** (only one `pending → submitted` transition can ever win, and only one `pending → rejected` transition can ever win), then: if satisfied, validate + translate via Phase 3 unchanged → `JobSubmissionService.submit()` → record `job_id`, mark `submitted`; if a `team` proposal just became impossible to satisfy, mark `rejected`.
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
