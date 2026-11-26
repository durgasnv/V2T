// local-dictation.js: Wispr-style dictation for any web page, powered by a local server.
//
//   Hold Ctrl+Shift+Space in a text field → speak → release → text is typed at the cursor.
//   Esc while holding cancels.
//
// Flow:  hotkey → mic (AudioWorklet, 16 kHz PCM16, 40 ms frames) → WebSocket to 127.0.0.1
//        → server returns {raw_text, text} → insert at the saved cursor position.
//
// Emits window events "localdictation" with detail.stage =
//   listening | partial | processing | result | error | idle
(() => {
  // Load only once per page. The page's own copy and the extension's copy run in separate
  // JS "worlds" but share the DOM, so we mark the <html> element.
  const root = document.documentElement;
  if (window.LocalDictation || root.hasAttribute("data-local-dictation")) return;
  root.setAttribute("data-local-dictation", "");

  const cfg = Object.assign({
    server: "ws://127.0.0.1:8765/v1/dictate",
    cleanup: true,               // run the AI cleanup step on the server
    language: "en",
    dictionary: [],              // exact spellings/hotwords for Whisper and cleanup
    style: "casual",
    editing: "medium",           // light | medium | heavy
    replacements: {},            // exact post-processing maps
    snippets: {},                // short codes expanded after cleanup
    secret: "",                  // optional SHARED_SECRET for extension mode
    fallbackTarget: null,        // element to type into when nothing editable is focused
    showPill: true,              // small floating status bubble
  }, window.LocalDictationConfig || {});

  // ---- 1. Audio capture: runs on the audio thread -----------------------------------------
  // Mic audio arrives at the device rate (usually 48 kHz). We average it down to 16 kHz,
  // convert to 16-bit integers, and post one 640-sample (40 ms) frame at a time. Wispr Flow
  // uses the same 640-sample chunks.
  const WORKLET = `
  class Pcm16Capture extends AudioWorkletProcessor {
    constructor() {
      super();
      this.ratio = sampleRate / 16000;   // e.g. 48000/16000 = 3 input samples per output
      this.t = 0; this.sum = 0; this.count = 0;
      this.frame = new Int16Array(640); this.n = 0;
    }
    process(inputs) {
      const ch = inputs[0] && inputs[0][0];
      if (!ch) return true;
      for (let i = 0; i < ch.length; i++) {
        this.sum += ch[i]; this.count++; this.t += 1;
        if (this.t >= this.ratio) {               // enough input for one 16 kHz sample
          this.t -= this.ratio;
          const s = Math.max(-1, Math.min(1, this.sum / this.count));
          this.sum = 0; this.count = 0;
          this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
          if (this.n === 640) {
            this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
            this.frame = new Int16Array(640); this.n = 0;
          }
        }
      }
      return true;
    }
  }
  registerProcessor("pcm16-capture", Pcm16Capture);`;

  // ---- 2. Small helpers --------------------------------------------------------------------
  const emit = (stage, extra = {}) =>
    window.dispatchEvent(new CustomEvent("localdictation", { detail: { stage, ...extra } }));

  let pill;
  function showPill(text) {
    if (!cfg.showPill) return;
    if (!pill) {
      pill = document.createElement("div");
      pill.style.cssText =
        "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:2147483647;" +
        "padding:8px 16px;border-radius:999px;background:#18181b;color:#fafafa;" +
        "font:500 13px/1.4 system-ui,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,.25);" +
        "max-width:70vw;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;pointer-events:none";
      document.documentElement.appendChild(pill);
    }
    pill.textContent = text;
    pill.style.display = text ? "block" : "none";
  }

  const isTextInput = (el) =>
    el instanceof HTMLTextAreaElement ||
    (el instanceof HTMLInputElement && /^(text|search|email|url|tel|)$/i.test(el.type));
  const isEditable = (el) => !!el && (isTextInput(el) || el.isContentEditable);

  // Read the text around the cursor. This is the "Textbox" context Wispr sends:
  // before_text, selected_text, after_text.
  function readContext(el) {
    let before = "", selected = "", after = "";
    if (isTextInput(el)) {
      const s = el.selectionStart ?? el.value.length, e = el.selectionEnd ?? s;
      before = el.value.slice(0, s); selected = el.value.slice(s, e); after = el.value.slice(e);
    } else if (el && el.isContentEditable) {
      const sel = getSelection();
      selected = sel ? sel.toString() : "";
      before = el.innerText || "";
    }
    const field = el && (el.getAttribute("aria-label") || el.getAttribute("name") ||
      el.getAttribute("placeholder") || el.dataset && el.dataset.placeholder || el.id || "");
    return { type: "context", site: location.hostname, title: document.title,
             field: String(field).slice(0, 120),
             before_text: before.slice(-1000), selected_text: selected.slice(0, 1000),
             after_text: after.slice(0, 1000) };
  }

  // Remember the cursor position, so we can type there even if focus moves while we wait.
  function saveCursor(el) {
    if (isTextInput(el)) return { start: el.selectionStart, end: el.selectionEnd };
    const sel = getSelection();
    return sel && sel.rangeCount ? { range: sel.getRangeAt(0).cloneRange() } : null;
  }

  // ---- 3. Typing the result into the page --------------------------------------------------
  function insertText(el, cursor, text) {
    if (!el || !text) return;
    el.focus();
    if (cursor && cursor.range) {
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(cursor.range);
    } else if (cursor && el.setSelectionRange) {
      el.setSelectionRange(cursor.start, cursor.end);
    }
    // Smart spacing: "Hello" + "world" becomes "Hello world".
    const before = readContext(el).before_text;
    if (before && !/\s$/.test(before) && !/^[.,!?;:)]/.test(text)) text = " " + text;
    // execCommand('insertText') acts like real typing: Ctrl+Z works, and React/Vue
    // see the change. If a site blocks it, fall back to setRangeText plus an input event.
    if (!document.execCommand("insertText", false, text)) {
      if (isTextInput(el)) {
        el.setRangeText(text, el.selectionStart, el.selectionEnd, "end");
        el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
      } else {
        navigator.clipboard && navigator.clipboard.writeText(text);
        showPill("Copied, press Ctrl+V to paste"); setTimeout(() => showPill(""), 2500);
      }
    }
  }

  // ---- 4. One dictation session ------------------------------------------------------------
  let session = null;   // { ws, audioCtx, stream, node, target, cursor }

  function fail(message) {
    cleanupAudio();
    if (session && session.ws) try { session.ws.close(); } catch (_) {}
    session = null;
    emit("error", { message });
    showPill("⚠ " + message); setTimeout(() => showPill(""), 3000);
  }

  function cleanupAudio(s = session) {
    if (!s) return;
    try { s.node && s.node.disconnect(); } catch (_) {}
    s.stream && s.stream.getTracks().forEach((t) => t.stop());
    s.audioCtx && s.audioCtx.close().catch(() => {});
    s.node = s.stream = s.audioCtx = null;
  }

  async function start() {
    if (session) return;
    let target = document.activeElement;
    if (!isEditable(target)) target = cfg.fallbackTarget;
    if (!isEditable(target)) return;              // nowhere to type: do nothing

    const s = session = { target, cursor: saveCursor(target), pending: [], recording: true };
    emit("listening"); showPill("● Listening…");

    // Open the connection and the mic at the same time, to save a few hundred ms.
    const ws = s.ws = new WebSocket(cfg.server);
    ws.binaryType = "arraybuffer";
    const opened = new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("Local server not reachable. Is server.py running?"));
    });
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state" && s.recording) {
        emit("partial", { text: msg.raw_text }); showPill("● " + (msg.raw_text || "Listening…"));
      } else if (msg.type === "result") {
        insertText(s.target, s.cursor, msg.text);
        emit("result", msg); showPill("");
        ws.close(); if (session === s) session = null;
        emit("idle");
      } else if (msg.type === "error") {
        fail(msg.message);
      }
    };
    opened.catch(() => {});                       // handled below; avoids console noise
    ws.onclose = () => { if (session === s && !s.recording) fail("Connection closed before result"); };

    try {
      s.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      s.audioCtx = new AudioContext();
      // Inside the extension, load the worklet from the extension's own file: strict sites
      // (Gmail etc.) block blob: scripts with their Content-Security-Policy.
      const inExtension = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id;
      const url = inExtension ? chrome.runtime.getURL("pcm16-worklet.js")
        : URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await s.audioCtx.audioWorklet.addModule(url);
      s.node = new AudioWorkletNode(s.audioCtx, "pcm16-capture");
      // Frames captured before the socket opens are queued, then sent.
      s.node.port.onmessage = (e) =>
        ws.readyState === WebSocket.OPEN ? ws.send(e.data) : s.pending.push(e.data);
      s.audioCtx.createMediaStreamSource(s.stream).connect(s.node);
      if (session !== s) return cleanupAudio(s);  // cancelled while the mic was starting

      await opened;
      ws.send(JSON.stringify({
        type: "init", language: cfg.language, cleanup: cfg.cleanup,
        dictionary: cfg.dictionary, style: cfg.style, editing: cfg.editing,
        replacements: cfg.replacements, snippets: cfg.snippets, secret: cfg.secret,
      }));
      ws.send(JSON.stringify(readContext(target)));
      s.pending.forEach((buf) => ws.send(buf)); s.pending = [];
      if (!s.recording) finish(s, false);         // key released during setup
    } catch (err) {
      const m = err && err.name === "NotAllowedError" ? "Microphone permission denied" : err.message;
      fail(m);
    }
  }

  function finish(s, cancel) {
    cleanupAudio(s);
    if (s.ws.readyState !== WebSocket.OPEN) return;   // start() will finish once it opens
    if (cancel) {
      s.ws.send(JSON.stringify({ type: "cancel" })); s.ws.close();
      if (session === s) session = null;
      showPill(""); emit("idle");
    } else {
      s.pending.forEach((buf) => s.ws.send(buf)); s.pending = [];
      s.ws.send(JSON.stringify({ type: "commit" }));
      emit("processing"); showPill("… Transcribing");
    }
  }

  function stop(cancel = false) {
    const s = session;
    if (!s || !s.recording) return;
    s.recording = false;
    if (cancel && s.ws.readyState !== WebSocket.OPEN) {
      cleanupAudio(s); try { s.ws.close(); } catch (_) {}
      session = null; showPill(""); emit("idle"); return;
    }
    finish(s, cancel);
  }

  // ---- 5. Hotkey: hold Ctrl+Shift+Space ----------------------------------------------------
  addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.ctrlKey && e.shiftKey) {
      e.preventDefault(); e.stopPropagation();
      if (!e.repeat) start();
    } else if (e.key === "Escape" && session && session.recording) {
      e.preventDefault(); stop(true);
    }
  }, true);
  addEventListener("keyup", (e) => {
    if (session && session.recording && (e.code === "Space" || e.key === "Control" || e.key === "Shift")) {
      e.preventDefault(); stop(false);
    }
  }, true);

  window.LocalDictation = { start, stop, config: cfg, isActive: () => !!(session && session.recording) };
})();
