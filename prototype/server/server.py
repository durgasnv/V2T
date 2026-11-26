"""Local dictation server.

  Browser ──WebSocket──► this server ──► Whisper (speech→text) ──► Ollama (cleanup) ──► back to browser

Protocol (modelled on Wispr Flow's flow_api.v1 TranscribeStream):
  client → {"type":"init", "language":"en", "cleanup":true}
  client → {"type":"context", "site":..., "before_text":..., "selected_text":...}
  client → binary frames: PCM16 little-endian, mono, 16 kHz, 640 samples each
  client → {"type":"commit"}   (key released)    or    {"type":"cancel"}  (Esc)
  server → {"type":"state", "raw_text":...}       (live preview while speaking)
  server → {"type":"result", "raw_text", "text", "audio_seconds", "asr_ms", "cleanup_ms", "cleanup_error"}

Run:  python server/server.py        then open http://localhost:8765
"""
import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from pipeline import ASR_PROMPT_CHARS, apply_postprocessing

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2          # 16-bit samples
MIN_AUDIO_SECONDS = 0.3                     # shorter than this = accidental tap
PREVIEW_WINDOW_SECONDS = 4                  # live preview shows only the most recent words
HERE = Path(__file__).resolve().parent
SDK_FILE = HERE.parent / "sdk" / "local-dictation.js"


def create_app(pipeline, allowed_origins: set[str], partials: bool = True,
               shared_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="Local Dictation")
    model_lock = asyncio.Lock()             # one Whisper run at a time (CPU-bound)

    async def transcribe(pcm: bytes, init: dict, ctx: dict) -> tuple[str, int]:
        if len(pcm) < BYTES_PER_SECOND * MIN_AUDIO_SECONDS:
            return "", 0
        prompt = (ctx.get("before_text") or "")[-ASR_PROMPT_CHARS:]
        t0 = time.monotonic()
        async with model_lock:
            # Whisper is slow, blocking work, so run it in a thread to keep the server responsive.
            text = await asyncio.to_thread(
                pipeline.transcribe, pcm, init.get("language"), prompt, init.get("dictionary"))
        return text, int((time.monotonic() - t0) * 1000)

    async def finish(pcm: bytes, init: dict, ctx: dict) -> dict:
        raw, asr_ms = await transcribe(pcm, init, ctx)
        fmt_ctx = dict(ctx)
        for key in ("dictionary", "style", "editing", "replacements", "snippets"):
            if key in init:
                fmt_ctx[key] = init[key]
        text, cleanup_ms, cleanup_error = raw, None, None
        if raw and init.get("cleanup", True):
            t0 = time.monotonic()
            try:
                text = await pipeline.cleanup(raw, fmt_ctx)
                cleanup_ms = int((time.monotonic() - t0) * 1000)
            except Exception as e:          # Ollama off or model missing: still return the raw text
                text, cleanup_error = raw, f"{type(e).__name__}: {e}"
        text = apply_postprocessing(text, fmt_ctx)
        return {"type": "result", "raw_text": raw, "text": text,
                "audio_seconds": round(len(pcm) / BYTES_PER_SECOND, 2),
                "asr_ms": asr_ms, "cleanup_ms": cleanup_ms, "cleanup_error": cleanup_error}

    @app.get("/")
    def demo_page():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/sdk.js")
    def sdk():
        return FileResponse(SDK_FILE, media_type="application/javascript")

    @app.websocket("/v1/dictate")
    async def dictate(ws: WebSocket):
        origin = ws.headers.get("origin", "")
        if allowed_origins and origin not in allowed_origins:
            await ws.close(code=4403)       # unknown website: refuse
            return
        await ws.accept()
        init, ctx, pcm = {}, {}, bytearray()
        preview_task: asyncio.Task | None = None
        last_preview_bytes = 0
        committed = False

        async def send_preview(audio: bytes):
            text, _ = await transcribe(audio, init, ctx)
            if not committed:
                await ws.send_json({"type": "state", "raw_text": text})

        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:                 # audio frame
                    pcm.extend(msg["bytes"])
                    # Live preview. It runs in the BACKGROUND so this loop keeps reading audio
                    # (a blocking preview lets frames pile up and delays the final result).
                    # Only the last few seconds are previewed, so each preview stays quick,
                    # and at most one runs at a time.
                    if (partials and len(pcm) - last_preview_bytes >= BYTES_PER_SECOND
                            and (preview_task is None or preview_task.done())
                            and not model_lock.locked()):
                        last_preview_bytes = len(pcm)
                        window = bytes(pcm[-PREVIEW_WINDOW_SECONDS * BYTES_PER_SECOND:])
                        preview_task = asyncio.create_task(send_preview(window))
                    continue
                data = json.loads(msg.get("text") or "{}")
                kind = data.get("type")
                if kind == "init":
                    if shared_secret and data.get("secret") != shared_secret:
                        await ws.close(code=4403)
                        return
                    init, pcm, committed = data, bytearray(), False
                    last_preview_bytes = 0
                elif kind == "context":
                    ctx = data
                elif kind == "cancel":
                    pcm = bytearray()
                    last_preview_bytes = 0
                elif kind == "commit":
                    committed = True                             # late previews are not sent
                    await ws.send_json(await finish(bytes(pcm), init, ctx))
                    pcm = bytearray()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            try:
                await ws.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            if preview_task and not preview_task.done():
                preview_task.cancel()

    return app


def main():
    import uvicorn
    from pipeline import Pipeline

    port = int(os.getenv("PORT", "8765"))
    # By default, only the demo page may connect. Set ALLOWED_ORIGINS="*" to allow any
    # website (needed for the browser extension, which connects from each site's origin).
    env = os.getenv("ALLOWED_ORIGINS", "")
    if env.strip() == "*":
        allowed = set()
    else:
        allowed = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
        allowed |= {o.strip() for o in env.split(",") if o.strip()}
    secret = os.getenv("SHARED_SECRET") or None
    print("Loading Whisper model (first run downloads ~500 MB)…")
    pipeline = Pipeline()
    print(f"Ready → open http://localhost:{port}")
    print("Allowed origins:", "ANY (extension mode)" if not allowed else ", ".join(sorted(allowed)))
    # 127.0.0.1 only: other computers on your network cannot reach this server.
    uvicorn.run(create_app(pipeline, allowed, shared_secret=secret),
                host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
