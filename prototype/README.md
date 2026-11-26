# Local Dictation: a Wispr Flow–style prototype

Hold **Ctrl + Shift + Space** in any text box, speak, and release. Your words are typed at the cursor.
Everything runs **on this computer**: speech recognition (Whisper) and cleanup (a small local AI via Ollama).

```
 Browser                                   This PC (127.0.0.1:8765)
 ┌──────────────────────────┐  WebSocket  ┌─────────────────────────────────────┐
 │ local-dictation.js       │  ─────────► │ server.py                           │
 │  hotkey → mic → 16 kHz   │  audio      │  1. Whisper small.en  (speech→text) │
 │  audio frames (40 ms)    │             │  2. Ollama qwen2.5:1.5b (cleanup)   │
 │  types result at cursor  │ ◄─────────  │                                     │
 └──────────────────────────┘  text       └─────────────────────────────────────┘
```

## Folder map

| Path | What it is |
|---|---|
| `server/server.py` | The local server: receives audio, returns text |
| `server/pipeline.py` | The two AI steps: Whisper (speech→text) and Ollama (cleanup) |
| `server/static/index.html` | Demo page with the "what just happened" panel |
| `sdk/local-dictation.js` | The dictation script: hotkey, mic, sending, typing |
| `extension/` | Chrome extension that injects the same script into every website |
| `tests/` | Automated tests + an end-to-end smoke test |
| `DESIGN.md`, `docs/plan.md` | Design and build plan |

## One-time setup (WSL / Linux, Python 3.10+)

```bash
cd prototype
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
ollama pull qwen2.5:3b            # optional: the cleanup model (~2 GB)
```

If `qwen2.5:3b` is not installed, the server automatically falls back to `qwen2.5:1.5b`
(~1 GB), which is faster but occasionally changes pronouns. Set `LLM_MODEL` and
`LLM_FALLBACK_MODEL` to choose explicitly.

The first server start downloads the Whisper model (~480 MB). If the download stalls, start it with
`HF_HUB_DISABLE_XET=1`.

## Run

```bash
# Terminal 1: cleanup model (skip if Ollama already runs as a service)
ollama serve

# Terminal 2: dictation server
.venv/bin/python server/server.py               # demo page only
ALLOWED_ORIGINS="*" .venv/bin/python server/server.py   # also allow the extension on any site
```

Open **http://localhost:8765** in Chrome on Windows (WSL forwards `localhost` automatically).

## Install the extension (works on any website)

1. Run `./tools/sync-extension.sh` after any change to `sdk/local-dictation.js`.
2. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick the `extension` folder.
3. Start the server with `ALLOWED_ORIGINS="*"`.
4. On any site, click in a text box and hold **Ctrl+Shift+Space**. Allow the microphone the first time; Chrome may also ask to allow access to "local network devices". Allow that too.

## Tuning and custom vocabulary

Server environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `ASR_MODEL` | `small.en` | Whisper model. Use `small` for auto-detect/multilingual, `large-v3-turbo` with a GPU. |
| `ASR_DEVICE` / `ASR_COMPUTE_TYPE` | `cpu` / `int8` | Set `cuda` / `float16` on an NVIDIA GPU. |
| `ASR_BEAM` | `5` | Beam search width; `1` is greedy. |
| `ASR_PROMPT_CHARS` | `500` | How much text before the cursor primes spelling/style. |
| `LLM_MODEL` | `qwen2.5:3b` | Cleanup model; falls back on HTTP 404. |
| `LLM_FALLBACK_MODEL` | `qwen2.5:1.5b` | Used when the preferred model is not pulled. |
| `LLM_TIMEOUT` | `60` | Seconds allowed for cleanup. |
| `SHARED_SECRET` | unset | Require `secret` in `init` — recommended in extension mode. |

Page/extension config (`window.LocalDictationConfig` before loading `/sdk.js`, or
`LocalDictation.config` afterwards):

```js
window.LocalDictationConfig = {
  language: null,              // omit/null = Whisper auto-detect
  cleanup: true,
  dictionary: ["Kubernetes", "Durga"],   // Whisper hotwords + exact spellings in cleanup
  style: "formal",             // casual | formal | genz
  editing: "medium",           // light | medium | heavy
  replacements: { "Monday": "Mon" },     // exact map applied after the model
  snippets: { ";mtg": "meeting" },       // exact expansion applied after the model
  secret: "same-as-server",    // if SHARED_SECRET is set
};
```

The demo page exposes **Custom words**, **Style** and **Editing** controls. Replacements and
snippets are deliberately exact string maps; the LLM handles fillers, false starts and punctuation.

## Tests

```bash
.venv/bin/python -m pytest tests -q        # protocol tests (fake AI, ~5 s)
.venv/bin/python tests/smoke_real.py       # real speech → text test (server must be running)
.venv/bin/python tests/smoke_real.py --messy   # shows raw vs cleaned on "um… no wait…" speech

# Browser test: real Chromium with a fake microphone playing tests/messy.wav
npm install && npx playwright-core install chromium     # one time
ALLOWED_ORIGINS=http://127.0.0.1:8000 .venv/bin/python server/server.py   # in another terminal
node tests/browser_e2e.mjs ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
```

## 2-minute demo script for mentors

1. **The idea (15 s).** "Wispr Flow streams your voice to its cloud. I rebuilt the same flow so it runs entirely on my laptop. No internet, no audio leaves the machine."
2. **Demo page (45 s).** Open `http://localhost:8765`. Hold the keys and say:
   *"um so the meeting is on uh Tuesday at three no wait at four"*.
   Release. Point at the panel: step 2 shows the **raw** Whisper text with the "um"s, step 3 shows the **cleaned** text ("So the meeting is on Tuesday at 4."), with timings for each step.
3. **Toggle cleanup off, then add a custom word (15 s).** First dictate verbatim/faster. Then type `Kubernetes` into **Custom words**, set **Style** to formal, and dictate again to show context and vocabulary biasing.
4. **Any website (30 s).** Open Gmail, ChatGPT or any site with the extension, dictate into its text box. Same script, same server.
5. **How it maps to Wispr (15 s).** Same pipeline (16 kHz audio in 40 ms chunks → speech recognition → AI cleanup → type at cursor). The difference is where the AI runs: Wispr's cloud vs this PC.

**If something goes wrong live:** "Local server not reachable" means server.py isn't running. If cleanup shows "Skipped", Ollama isn't running, but the raw text still gets typed.

## Limits (prototype)

- CPU only: expect about 1–3 s for speech-to-text and 1–4 s for cleanup (`qwen2.5:3b` is slower than the 1.5b fallback).
- Browser only; system-wide dictation in desktop apps would need a native helper (as Wispr has).
- Default ASR model is English (`small.en`). For other languages set `ASR_MODEL=small` and `language: null` in the config.
- Live previews still re-transcribe a short trailing window; true incremental decoding is not implemented.
