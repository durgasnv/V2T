# Local Dictation Prototype — Meeting Report

## 1. What I did

1. **Studied the shipped Wispr Flow Windows app** (v1.6.1619) using static inspection only — no install, no execution, no network capture.
2. **Extracted the architecture and pipeline** from `app.asar`, the Electron main/renderer bundles, and `Wispr Flow Helper.exe`.
3. **Built a local, Wispr-style prototype**: hold **Ctrl+Shift+Space**, speak, release, and the cleaned text is typed at the cursor. Audio never leaves the machine.

---

## 2. Key insights from Wispr Flow

| Insight | Evidence / detail | Why it matters |
|---|---|---|
| ASR is **not local** | Audio is streamed over a bidirectional gRPC stream to `inference.wisprflow.com`; only language ID (fastText `lid.176`) and a speaker-embedding model (WeSpeaker ResNet34 ONNX) ship locally. | Privacy and latency depend on the cloud hop. My prototype removes that hop. |
| A native helper does the "system" work | .NET `Wispr Flow Helper.exe` installs a low-level keyboard hook, reads the focused textbox via UI Automation (text before/after cursor, selection, app name, screen words), and pastes with clipboard + `SendInput` Ctrl+V, then restores the clipboard. | The magic is not just speech-to-text; it is hotkey + context + reliable insertion. |
| Audio is captured in tiny chunks | Hidden renderer uses `getUserMedia`, `AudioContext({sampleRate: 16000})` and an `AudioWorklet` that emits **640-sample / 40 ms** Float32 chunks; packets are WAV/PCM or Opus behind a feature flag. | Streaming while speaking makes recognition mostly finish before key release. |
| Two-stage output | Server returns `raw_text` (ASR) and `formatted_text` (LLM cleanup), plus progress, confidence, SNR and volume. "Verbatim" mode skips the LLM. | Users can choose fidelity vs. polish; the UI can show both. |
| Context drives quality | `Context` carries surrounding text, screen words (accessibility + OCR), VS Code variable/file names, dictionary, replacements, snippets, style and editing strength. | Names/jargon and tone are corrected from the environment, not guessed. |
| Server likely runs multiple ASR engines | Candidate origins seen in code: `WHISPER`, `SCRIBE`, `VOXTRAL`, `QWEN`, `MIXTURE`; best candidate beam is chosen. *(Inferred from code, not observed live.)* | Accuracy is improved by ensembling, at extra server cost. |
| Failure handling is explicit | Heartbeats, commit timeouts, typed fallback reasons and retry logic exist; exact fallback path unresolved. | Dictation must degrade gracefully when the network or model fails. |

**Confidence note:** transport, message schema, audio format, context fields and Windows hook/paste mechanism are backed by static code evidence. Server-side models/prompts, real latency and the exact retry path were **not observed**.

---

## 3. What I built

A fully local equivalent with the same shape as Wispr:

```
Browser (any website)                         This PC — 127.0.0.1:8765
┌────────────────────────────┐                ┌──────────────────────────────┐
│ local-dictation.js         │   WebSocket   │ server.py (FastAPI)          │
│ • Ctrl+Shift+Space to talk │   ─────────►  │ 1. faster-whisper small.en   │
│ • AudioWorklet → PCM16     │   40 ms frames│ 2. Ollama qwen2.5:1.5b       │
│ • insert at cursor         │   ◄─────────  │    (optional cleanup)        │
└────────────────────────────┘    raw+clean   └──────────────────────────────┘
```

Two front ends share **one SDK**: a demo page that shows the pipeline, and a Chrome extension that proves it works on any website.

---

## 4. What I used, why, and how it works

### 4.1 Analysis / investigation choices

| What | Why I used it | How it works |
|---|---|---|
| Official update package (`WisprFlow-1.6.1619-full.nupkg`) | Source of truth for the shipped product; SHA-1 checked against the release feed. | Downloaded the release payload, unpacked it, and inspected files without running the app. |
| Static inspection of `app.asar` + `.webpack` bundles | Safe, repeatable, and enough to recover protocol names, IPC messages and UI logic. | Read Electron main/renderer JS; searched for `getContext`, `helper:updateShortcuts`, `startGrpcStream`, protobuf enums. |
| `Wispr Flow Helper.exe` strings/imports | The native side contains the OS-level behavior that the JS cannot do. | Inspected imports/strings for `SetWindowsHookEx`, UI Automation `TextPattern`, clipboard and `SendInput`. |
| No live run / no traffic capture | Avoided executing third-party code and avoided claiming unverified runtime behavior. | All inferences are labelled; server internals remain "inferred/unknown". |

### 4.2 Prototype choices

| Component / tool | Why I used it | How it works here |
|---|---|---|
| **Python + FastAPI + uvicorn** | Small, fast to build, native async WebSocket support; easy to keep on localhost. | Serves `/` (demo page), `/sdk.js`, and `WS /v1/dictate` on `127.0.0.1:8765` only. |
| **faster-whisper `small.en`, int8, CPU** | Best accuracy/size trade-off on this machine; no GPU needed; supports `initial_prompt` and `hotwords` for custom vocabulary. | Loaded once at startup (warm model); transcribes the accumulated PCM buffer on `commit`. |
| **Ollama `qwen2.5:1.5b`** | Small local instruction model, quick on CPU, enough to remove fillers and fix punctuation; fully offline. | Called at `http://127.0.0.1:11434/api/chat` with a few-shot, `temperature: 0` prompt; failure falls back to raw text. |
| **Raw PCM16 over WebSocket (not Opus)** | On localhost, compression adds CPU/complexity with no bandwidth benefit. | Mono 16 kHz, little-endian, 640 samples = 1280-byte binary frames. |
| **JSON control messages modelled on `flow_api.v1`** | Keeps the design recognisably Wispr-like while staying simple to debug. | Client sends `init`, `context`, binary audio, then `commit` or `cancel`; server sends `state` partials and a final `result` with timings. |
| **Vanilla JS + AudioWorklet (no framework)** | Tiny SDK that can be injected into any page; no build step, no dependencies. | Hotkey listener starts `getUserMedia`; the worklet posts 40 ms frames to the socket; results are inserted with `insertText` → `setRangeText` → clipboard fallback. |
| **One shared SDK file** | Demo page explains the internals; extension proves "any website" without duplicating logic. | `tools/sync-extension.sh` copies `sdk/local-dictation.js` into the extension and writes the worklet as a separate file for strict-CSP sites. `data-local-dictation` marker prevents double typing when both are present. |
| **Chrome MV3 content script** | Works on sites I do not control, without changing them. | Injects the SDK, exposes microphone permission, and uses `web_accessible_resources` for the worklet. |
| **`ALLOWED_ORIGINS` allow-list / 127.0.0.1 binding** | Stops arbitrary websites from using the local microphone server. | Disallowed origins get WebSocket close code `4403`; server never binds `0.0.0.0`. |
| **pytest + real smoke test + Playwright e2e** | Unit tests cover protocol edge cases; smoke test proves real ASR; browser test proves real insertion. | Fake pipeline tests (7–8 tests), `smoke_real.py` with a WAV sample, and headless Chromium with a fake microphone. |

---

## 5. How it works, end to end

1. **Start:** user holds Ctrl+Shift+Space in an editable field. The SDK opens a WebSocket and sends `init` (language, style, cleanup on/off, dictionary) and `context` (site, field, text before/after selection).
2. **Capture:** microphone audio is resampled to 16 kHz mono and emitted as 640-sample PCM16 frames by an AudioWorklet. Frames stream continuously while the key is held.
3. **Partial feedback:** the server can return `state` messages with partial raw text roughly once per second. In the prototype, live preview runs only on the last 4 seconds so it cannot block the final result.
4. **Commit:** key release sends `commit`; Esc sends `cancel` and drops the buffer. Very short audio (<0.3 s) returns empty text.
5. **Recognise:** faster-whisper transcribes the buffer using context words as a prompt/hotwords → `raw_text`.
6. **Clean up (optional):** if enabled, Ollama rewrites fillers ("um", "uh", false starts) while preserving names and meant text → `text`. If Ollama is unavailable, `text = raw_text` and `cleanup_error` is reported.
7. **Insert:** the SDK types the final text at the cursor using progressively safer fallback methods.
8. **Observe:** the demo page shows raw text, cleaned text, `asr_ms`, `cleanup_ms` and errors, making the pipeline visible for the meeting demo.

---

## 6. Validation and results

- **Protocol tests:** 8 passing (`pytest tests -q`), including cleanup off, short audio, cancel, Ollama failure fallback, and disallowed origin (`4403`).
- **Real ASR smoke test:** PASS — *"The quick brown fox jumps over the lazy dog."* Warm run ≈ **2.0 s ASR + 0.9 s cleanup** on CPU.
- **Browser e2e:** 8/8 checks in headless Chromium with a fake microphone; textarea and contenteditable both work.
- **Extension:** verified on a real origin with a textarea and contenteditable; server-unreachable case shows a clear error instead of hanging.
- **Bug found and fixed:** blocking live preview made frames pile up and delayed results; preview was moved to background on the last 4 seconds only.

---

## 7. Limits and next steps

- **Limits:** CPU only (1–3 s per stage), English `small.en` model, browser only (no global hotkey in desktop apps), mic permission asked once per website, 1.5B cleanup occasionally changes a pronoun.
- **Quick wins:** larger cleanup model (`qwen2.5:3b`) for fewer pronoun errors; offscreen document for one mic permission; Opus only if this ever runs over a real network.
- **To match Wispr fully:** add a small native helper for global hotkey, UI Automation context and clipboard-safe paste into any desktop app.
- **Security:** keep `127.0.0.1` binding, keep origin allow-list/secret, treat dictated and page text as data in prompts to avoid injection, do not log audio/transcripts.

---

## 8. Improvements implemented after review

1. **Better cleanup default** — `qwen2.5:3b` is now preferred, with automatic fallback to `qwen2.5:1.5b` if it is not pulled; this addresses pronoun drift without breaking the demo.
2. **Exact-term preservation** — `dictionary` feeds Whisper hotwords and the cleanup prompt; `replacements` and `snippets` are applied as exact maps after the model, so names and codes cannot be paraphrased.
3. **Richer context** — `selected_text`, `after_text`, field label, page title and style/editing strength now reach the cleanup model; much more of `before_text` is used for ASR prompting.
4. **Flexible ASR** — `ASR_DEVICE`, `ASR_COMPUTE_TYPE`, `ASR_MODEL`, `ASR_BEAM` and `language: null` (auto-detect) are configurable.
5. **Lower latency** — live previews now wait for one second of new audio instead of re-running immediately, reducing CPU contention before the final pass.
6. **Security** — optional `SHARED_SECRET` in `init` complements the origin allow-list for extension mode.
7. **Tests** — 16 protocol/prompt/fallback tests now pass, including regression coverage for hotwords, replacements on LLM failure, prompt-injection guard and model fallback.

## 9. 90-second meeting script

> "I inspected the shipped Wispr Flow app without running it and recovered its pipeline: a native helper handles hotkey, context and paste; audio streams as 16 kHz, 40 ms chunks over gRPC to the cloud; the server returns raw text and LLM-cleaned text. The insight worth copying is that quality comes from streaming plus context plus two-stage output, not just ASR.
>
> I rebuilt that flow locally: a browser SDK captures audio with an AudioWorklet, sends PCM16 frames over a WebSocket to a FastAPI server on `127.0.0.1`, runs faster-whisper `small.en` and an optional Ollama cleanup model, then types at the cursor. I used Whisper for offline accuracy, a 1.5B model for fast cleanup, raw PCM because localhost has no bandwidth problem, and one shared SDK for both the demo page and the Chrome extension.
>
> Results: 16 tests now pass, a real speech clip transcribes correctly in about 2 seconds ASR plus 1 second cleanup, and browser tests pass on textarea and contenteditable. We also added custom vocabulary, exact replacements, richer context, a stronger cleanup model with fallback, and a shared-secret option. Limits are CPU latency, English-only by default, and browser-only; a native helper would be the next step for system-wide dictation."
