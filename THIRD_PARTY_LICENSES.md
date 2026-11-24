# Third-Party Components & Commercial Licensing Compliance

HOVITA's core platform is MIT licensed. External providers and optional self-hosted engines sit behind the 12 swappable module interfaces so commercial deployments can pick commercial-safe providers without modifying core logic:

| Component | License | Commercial Policy |
|---|---|---|
| LangGraph / LangChain Core | MIT | Core agent runtime (always allowed) |
| FastAPI / SQLAlchemy / Alembic / Pydantic v2 | MIT | Core backend stack |
| PostgreSQL 16 + pgvector | PostgreSQL / MIT | Production persistence & scoped vector store |
| Wan Animate 2.2 / Wan 2.2 | Apache 2.0 | Motion transfer & video generation |
| FLUX.1 [schnell] / FLUX.2 [klein] 4B / Qwen-Image | Apache 2.0 | Base image generation |
| pocket-tts-farsi / Kokoro-82M / Chatterbox | Verify model card / Apache 2.0 / MIT | Default TTS stack (ParsVoice-XTTS / Coqui XTTS v2 CPML non-commercial excluded from default commercial config) |
| faster-whisper | MIT | Local speech-to-text |
| FFmpeg / PySceneDetect | LGPL-2.1+ / Apache 2.0 | Video composition & scene detection |
| InsightFace (code) | MIT (verify weight cards) | ArcFace face similarity scoring |
| n8n (optional external bridge) | Sustainable Use License | Only enabled when customer self-hosts or holds commercial license |
| MinIO (optional self-hosted S3) | AGPL-3.0 | S3-compatible interface; swap with AWS S3 / R2 / local volume if commercial AGPL restriction applies |
