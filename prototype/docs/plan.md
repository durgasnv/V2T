# Local Dictation Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fully local, Wispr-style dictation prototype: Python server (Whisper + optional Ollama cleanup), a demo page showing the pipeline, and a Chrome extension for any site.

**Architecture:** The browser captures 16 kHz PCM16 in 640-sample frames and streams them over a WebSocket to a FastAPI server on 127.0.0.1:8765. The server runs faster-whisper and then optional Ollama cleanup, and returns raw text, cleaned text and timings. One SDK file is shared by the demo page and the extension.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, faster-whisper, httpx, numpy, pytest; vanilla JS (AudioWorklet); Chrome MV3.

**Spec:** `prototype/DESIGN.md`

## Global Constraints
- Server binds `127.0.0.1:8765` only.
- ASR: `small.en`, `device="cpu"`, `compute_type="int8"`. Cleanup: Ollama `qwen2.5:1.5b` at `http://127.0.0.1:11434/api/chat`.
- Audio: PCM16 LE, mono, 16000 Hz, 640-sample frames.
- Hotkey: hold Ctrl+Shift+Space; Esc cancels.
- Simple, clean UI; no design polish.
- No git repo exists; no commits unless the user asks.

## Review Focus
1. Server not running: the SDK shows "Local server not reachable" and doesn't hang. (Task 3, manual check)
2. Ollama down or model missing: the result still returns the raw text, with `cleanup_ms: null` and `cleanup_error` set. (Task 1 test `test_llm_failure_falls_back_to_raw`)
3. Very short press (< 0.3 s audio): the result is empty and nothing is inserted. (Task 1 test `test_short_audio_returns_empty`)
4. Hotkey pressed outside an editable field: on the demo page, dictation goes into the demo textarea; on other sites it does nothing. (Task 3 manual)
5. Disallowed Origin: the socket closes with code 4403. (Task 1 test `test_rejects_disallowed_origin`)

---

### Task 1: Server protocol + pipeline (fake ASR)

**Files:** Create `server/pipeline.py`, `server/server.py`, `server/requirements.txt`, `tests/test_server.py`

**Interfaces:**
- `pipeline.py`:
  - `class Pipeline: transcribe(pcm: bytes, language: str|None, prompt: str) -> str`
  - `async cleanup(raw: str, ctx: dict) -> str`
  - `build_cleanup_messages(raw: str, ctx: dict) -> list[dict]`
- `server.py`: `create_app(pipeline, allowed_origins: set[str]) -> FastAPI`
  - WS `/v1/dictate`
  - GET `/` → `static/index.html`
  - GET `/sdk.js` → `../sdk/local-dictation.js`
- Result message: `{"type":"result","raw_text":str,"text":str,"audio_seconds":float,"asr_ms":int,"cleanup_ms":int|None,"cleanup_error":str|None}`

- [ ] Step 1: write tests using a `FakePipeline`:
  - `test_commit_returns_result`: after init(cleanup=True), 1 s of frames, then commit, `raw_text=="hello world"` and `text=="Hello world."`
  - `test_cleanup_off_returns_raw`
  - `test_short_audio_returns_empty`: 0.1 s of audio gives `text==""`
  - `test_cancel_clears_buffer`
  - `test_llm_failure_falls_back_to_raw`
  - `test_rejects_disallowed_origin`: close code 4403
- [ ] Step 2: run `pytest tests -q`. Expected: fail (no module).
- [ ] Step 3: implement. Run cleanup only when `init.cleanup` is true, and catch exceptions so they become `cleanup_error`.
- [ ] Step 4: run `pytest tests -q`. Expected: all pass.

### Task 2: Real ASR smoke test

**Files:** Create `tests/make_sample.ps1` (Windows TTS → wav), `tests/smoke_real.py`

- [ ] Step 1: generate `tests/sample.wav` with Windows `System.Speech` saying "The quick brown fox jumps over the lazy dog."
- [ ] Step 2: `smoke_real.py` resamples to 16 kHz, streams over a real WebSocket to the running server in 640-sample frames, commits, and prints the result. Pass when the lowercased raw text contains "quick brown fox".

### Task 3: SDK + demo page

**Files:** Create `sdk/local-dictation.js`, `server/static/index.html`

**Interfaces:**
- `window.LocalDictation = {start(), stop(cancel?), config}`
- `window.LocalDictationConfig`: `{server, cleanup:boolean, fallbackTarget:Element|null}`
- DOM events on `window`, `CustomEvent("localdictation", {detail:{stage, ...}})`, where `stage` is one of `listening` | `partial` | `processing` | `result` | `error` | `idle`.
- The demo page listens to these events to fill the pipeline panel. Its mic button toggles start and stop.

- [ ] Manual check: open `http://localhost:8765`, hold the hotkey, speak, release. Text appears and the panel shows raw, cleaned and timings. With the server stopped, an error shows.

### Task 4: Extension + README

**Files:** Create `extension/manifest.json`, `extension/local-dictation.js` (copied by `tools/sync-extension.sh`), `README.md`

- [ ] Manual check: load the unpacked extension, then dictate into a real site's textarea.
- [ ] The README covers setup, run commands, and a 2-minute demo script.
