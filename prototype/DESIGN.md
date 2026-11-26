# Local Dictation Prototype — Design

## Intent
Show mentors a working, fully local version of Wispr Flow's voice-to-text feature:
first explained on a demo page (pipeline visible), then proven inside real websites
via a Chrome extension. Simple, clean UI; visual polish deferred.

## Approaches considered
- **A. Demo page only:** best for explaining the pipeline, but doesn't prove "any website".
- **B. Extension only:** proves "any website", hides the internals.
- **C. Both (chosen by user):** the demo page explains, the extension proves. They share one dictation script.

## Components
 1. `server/server.py`: FastAPI on 127.0.0.1:8765.
    - faster-whisper `small.en`, int8 on CPU; configurable device/compute type and language auto-detect.
    - Optional Ollama cleanup with `qwen2.5:3b`, falling back to `qwen2.5:1.5b` if missing.
    - Exact replacements/snippets applied after the model.
    - WebSocket `/v1/dictate`; serves the demo page and `/sdk.js`. Optional `SHARED_SECRET`.
 2. `server/static/index.html`: text box, mic button, "Clean up with AI" toggle, custom-word/style/editing controls, and a pipeline panel showing raw text, cleaned text and timings.
 3. `sdk/local-dictation.js`: hold Ctrl+Shift+Space to dictate, Esc to cancel. Sends dictionary/style/editing/replacements/snippets. Inserts at the cursor. Emits events the demo page listens to.
4. `extension/`: MV3 content script injecting the same SDK on all sites.
5. `README.md`: setup steps plus a 2-minute demo script.

## Protocol
JSON + binary over WebSocket, modelled on Wispr's `flow_api.v1`:
- Client sends `init` (language, cleanup, dictionary, style, editing, replacements, snippets, secret), `context` (site, title, field, before/selected/after text), PCM16 16 kHz frames (640 samples each), then `commit` or `cancel`.
- Server replies with `state` (partials) and `result` (raw text, cleaned text, timings, cleanup error).
- Live previews use only the last 4 s and at most once per second of new audio, so they cannot block the final result.

## Non-goals
Desktop-wide (non-browser) dictation, design polish, GPU setup, multi-user.

## Testing
Automated: a server smoke test that transcribes a sample clip over the WebSocket. Manual: a Chrome run-through on the demo page and on one real site.
