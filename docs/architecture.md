# HOVITA Architecture Decisions & System Contracts

## 1. Six Logical Layers

1. **L1 — User Interface (`web/`)**: Next.js 15 RTL console covering conversational persona creation, 8-step onboarding wizard, identity/voice/product studio, social account connect & audit, human approval queue (`L0`–`L3`), direct chat, live WebRTC call, cost dashboard, and global emergency stop.
2. **L2 — Workflow & Control (`contracts/`, `agents/onboarding.py`, `agents/social_connect.py`)**: Stateful conversational brief generation (`<15` questions), autonomy-to-publish mapping (`L0`=`produce_only`, `L1`=`approval_required`, `L2`/`L3`=`autonomous` with sensitive-topic guard), and 8-step setup persistence.
3. **L3 — Brain (`agents/planner_agent.py`, `agents/producer_agent.py`, `agents/quality_gates.py`, `agents/learning_job.py`, `scheduler/`)**:
   - **Orchestrator**: LangGraph (`MIT`) with persistent checkpointer (`SqliteSaver` / `PostgresSaver`) + `StageCheckpoint` per pipeline step. Optional customer-hosted/licensed `n8n` webhook bridge gated behind `N8N_ENABLED=true`.
   - **Two Agents**: Planner Agent (`TaskGraph` + `DecisionLog`) and Producer Agent (11-stage checkpointed pipeline).
   - **Two Non-Agentic Services**: Deterministic 7-Gate Quality Service and Weekly Analytics & Learning Job.
4. **L4 — Media & Verification (`modules/{image_gen,video_gen,motion_transfer,tts,stt,video_edit}`)**: ComfyUI / FLUX.2 / IP-Adapter / LoRA (`ArcFace >= 0.72`), Wan Animate 2.2 / Hunyuan / LTX, `pocket-tts-farsi` / Kokoro, `faster-whisper`, and FFmpeg + C2PA metadata injection.
5. **L5 — Connectors (`modules/{publisher,social_analytics,research}`, `resources/browser_layer.py`, `agents/live_session.py`)**: Official Graph/Content Posting/Data APIs with idempotency keys and quota counters; read-only Playwright visual inspection; LiveKit + avatar turn pipeline (`<2s` SLA).
6. **L6 — Data & 4-Layer Memory (`db/`, `modules/memory/`, `resources/storage.py`)**:
   - **Layer 1**: Immutable/versioned `character_profile` (Source of Truth).
   - **Layer 2**: Scoped vector blackboard (`memory_entry`, strictly filtered by `character_id` and `status='active'`).
   - **Layer 3**: Checkpointed workflow state (`stage_checkpoint` + LangGraph saver).
   - **Layer 4**: Post-interaction extraction with 3-conflict reality invalidation (`status='invalidated'`, never hard-deleted).

## 2. Single-Persona Isolation Model

Each installation enforces `singleton_lock = 1` with `UNIQUE(singleton_lock)` on `character_profile` and `emergency_state`, plus repository guards (`assert_single_persona_id`). Scaling across multiple personas is achieved strictly by cloning the deployment (`scripts/clone_install.sh`), guaranteeing zero data, memory, identity, or token leakage across personas.

## 3. Economic & Resource Governance

- **Cost Formula**: Every execution records `JobRun` cost as `tin*Rin + tout*Rout + sec_video*Rv + img*Ri + min_call*Rc + gpu_min*Rg + energy_kwh`.
- **Daily Budget Guardrail**: Warns and defers low-priority work at `80%` of `DAILY_BUDGET_USD`; halts non-critical execution at `100%`.
- **Revision Cap**: Maximum `2` regeneration attempts per content item before human escalation.
- **Free-First Escalation**: `S0` (local CPU/GPU) → `S1` (free API key rotation on `429`/`5xx`) → `S2` (Eco API, only on QA failure or `>=25%` quality gain) → `S3` (Pro API) → `S4` (safe queued degradation).
- **GPU Lifecycle**: CPU always-on; GPU wakes on prioritized queue items (`1=scheduled_today > 2=normal > 3=experiment`), batches pending work, and enters sleep mode after `10` minutes of idle time (`0` idle GPU cost).

## 4. Mandatory 7 Quality Gates & Official Compliance

1. `hook_3s >= 6.0` (independent judge).
2. `face_consistency >= 0.72` (ArcFace cosine score against approved reference).
3. `audio_video_sync >= 95%`.
4. `style_goal_fit` (zero `dont`-list violations).
5. `duration_rhythm` (`15–45s` Reels, `>=1` cut per `5s`).
6. `safety_claims` (prompt-injection guardrail + strict `ProductItem` price/spec verification).
7. `ai_disclosure` (`ai_disclosure=true` + embedded C2PA metadata verified inside MP4).
