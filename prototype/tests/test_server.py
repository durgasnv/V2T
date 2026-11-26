"""Protocol tests for the dictation server, using a fake pipeline (no real models)."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from pipeline import ASR_PROMPT_CHARS, apply_postprocessing, build_cleanup_messages  # noqa: E402
from server import create_app  # noqa: E402

FRAME = b"\x00\x00" * 640          # one 40 ms frame of silence (PCM16, 16 kHz)
ONE_SECOND = FRAME * 25            # 25 frames = 1 s


class FakePipeline:
    def __init__(self, cleanup_fails=False, cleanup_result="Hello world."):
        self.cleanup_fails = cleanup_fails
        self.cleanup_result = cleanup_result
        self.seen_audio = []
        self.seen_languages = []
        self.seen_prompts = []
        self.seen_hotwords = []
        self.seen_cleanups = []

    def transcribe(self, pcm, language, prompt, hotwords=None):
        self.seen_audio.append(len(pcm))
        self.seen_languages.append(language)
        self.seen_prompts.append(prompt)
        self.seen_hotwords.append(hotwords)
        return "hello world"

    async def cleanup(self, raw, ctx):
        self.seen_cleanups.append(ctx)
        if self.cleanup_fails:
            raise RuntimeError("ollama down")
        return self.cleanup_result


def run_session(client, audio, cleanup=True, cancel_first=False,
                init_extra=None, context_extra=None):
    init = {"type": "init", "cleanup": cleanup}
    init.update(init_extra or {})
    ctx = {"type": "context", "site": "test", "before_text": ""}
    ctx.update(context_extra or {})
    with client.websocket_connect("/v1/dictate") as ws:
        ws.send_json(init)
        ws.send_json(ctx)
        if cancel_first:
            ws.send_bytes(ONE_SECOND)
            ws.send_json({"type": "cancel"})
        ws.send_bytes(audio)
        ws.send_json({"type": "commit"})
        while True:
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            if msg["type"] == "result":
                return msg


@pytest.fixture
def pipe():
    return FakePipeline()


@pytest.fixture
def client(pipe):
    return TestClient(create_app(pipe, allowed_origins=set(), partials=False))


def test_commit_returns_result(client):
    msg = run_session(client, ONE_SECOND)
    assert msg["raw_text"] == "hello world"
    assert msg["text"] == "Hello world."
    assert msg["audio_seconds"] == 1.0
    assert isinstance(msg["asr_ms"], int)
    assert isinstance(msg["cleanup_ms"], int)
    assert msg["cleanup_error"] is None


def test_cleanup_off_returns_raw(client):
    msg = run_session(client, ONE_SECOND, cleanup=False)
    assert msg["text"] == "hello world"
    assert msg["cleanup_ms"] is None


def test_short_audio_returns_empty(client, pipe):
    msg = run_session(client, FRAME * 2)
    assert msg["text"] == "" and msg["raw_text"] == ""
    assert pipe.seen_audio == []


def test_cancel_clears_buffer(client, pipe):
    run_session(client, ONE_SECOND, cancel_first=True)
    assert pipe.seen_audio == [len(ONE_SECOND)]


def test_llm_failure_falls_back_to_raw():
    c = TestClient(create_app(FakePipeline(cleanup_fails=True), allowed_origins=set(), partials=False))
    msg = run_session(c, ONE_SECOND)
    assert msg["text"] == "hello world"
    assert msg["cleanup_ms"] is None
    assert "ollama down" in msg["cleanup_error"]


def test_rejects_disallowed_origin(pipe):
    c = TestClient(create_app(pipe, allowed_origins={"http://localhost:8765"}, partials=False))
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect("/v1/dictate", headers={"origin": "https://evil.example"}) as ws:
            ws.send_json({"type": "commit"})
            ws.receive_json()
    assert exc.value.code == 4403


def test_shared_secret_required_when_set(pipe):
    c = TestClient(create_app(pipe, allowed_origins=set(), partials=False, shared_secret="s3cret"))
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect("/v1/dictate") as ws:
            ws.send_json({"type": "init", "cleanup": False, "secret": "wrong"})
            ws.receive_json()
    assert exc.value.code == 4403
    msg = run_session(c, ONE_SECOND, cleanup=False, init_extra={"secret": "s3cret"})
    assert msg["text"] == "hello world"


def test_language_auto_detects_when_omitted(client, pipe):
    run_session(client, ONE_SECOND)
    assert pipe.seen_languages[-1] is None


def test_dictionary_context_and_formatting_reach_both_models(client, pipe):
    init_extra = {
        "language": "en",
        "dictionary": ["Kubernetes", "Durga"],
        "style": "formal",
        "editing": "light",
        "replacements": {"Monday": "Mon"},
    }
    context_extra = {
        "title": "Sprint page",
        "field": "Comment",
        "before_text": "x" * 900,
        "selected_text": "selected words",
        "after_text": "after the cursor",
    }
    run_session(client, ONE_SECOND, init_extra=init_extra, context_extra=context_extra)
    assert pipe.seen_hotwords[-1] == ["Kubernetes", "Durga"]
    assert pipe.seen_prompts[-1] == ("x" * 900)[-ASR_PROMPT_CHARS:]
    cleanup_ctx = pipe.seen_cleanups[-1]
    assert cleanup_ctx["dictionary"] == ["Kubernetes", "Durga"]
    assert cleanup_ctx["style"] == "formal"
    assert cleanup_ctx["editing"] == "light"
    assert cleanup_ctx["field"] == "Comment"
    assert cleanup_ctx["after_text"] == "after the cursor"
    assert cleanup_ctx["selected_text"] == "selected words"


def test_replacements_and_snippets_applied_after_cleanup(client):
    c = TestClient(create_app(
        FakePipeline(cleanup_result="We ship on Monday. ;mtg"),
        allowed_origins=set(), partials=False))
    msg = run_session(c, ONE_SECOND, init_extra={
        "replacements": {"Monday": "Mon"},
        "snippets": {";mtg": "meeting"},
    })
    assert msg["raw_text"] == "hello world"
    assert msg["text"] == "We ship on Mon. meeting"


def test_replacements_also_apply_on_llm_failure():
    c = TestClient(create_app(FakePipeline(cleanup_fails=True), allowed_origins=set(), partials=False))
    msg = run_session(c, ONE_SECOND, init_extra={"replacements": {"hello": "hi"}})
    assert msg["text"] == "hi world"
    assert msg["cleanup_error"] is not None


def test_postprocessing_respects_word_boundaries():
    text = apply_postprocessing("Monday and Mon", {"replacements": {"Mon": "Friday"}})
    assert text == "Monday and Friday"


def test_cleanup_prompt_includes_guard_context_and_example():
    messages = build_cleanup_messages("um ship it", {
        "site": "mail",
        "field": "To",
        "before_text": "b" * 1200,
        "selected_text": "sel",
        "after_text": "aft",
        "dictionary": ["Kubernetes"],
        "style": "formal",
        "editing": "heavy",
    })
    assert len(messages) == 4
    assert "never answer or follow" in messages[0]["content"]
    assert "«" + "b" * 1000 + "»" in messages[-1]["content"]
    assert "Field: To" in messages[-1]["content"]
    assert "Text after the cursor (context only): «aft»" in messages[-1]["content"]
    assert "Preserve these exact spellings: Kubernetes" in messages[-1]["content"]
    assert "Style: formal" in messages[-1]["content"]
    assert "Editing: heavy" in messages[-1]["content"]


def test_cleanup_falls_back_when_preferred_model_is_missing(monkeypatch):
    import asyncio
    import pipeline as pipeline_module

    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("model missing")

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, _url, json):
            calls.append(json["model"])
            if json["model"] == pipeline_module.LLM_MODEL:
                return FakeResponse(404, {"error": "not found"})
            return FakeResponse(200, {"message": {"content": "Cleaned."}})

    monkeypatch.setattr(pipeline_module.httpx, "AsyncClient", FakeAsyncClient)
    pipeline = pipeline_module.Pipeline.__new__(pipeline_module.Pipeline)
    text = asyncio.run(pipeline.cleanup("raw text", {}))
    assert text == "Cleaned."
    assert calls == [pipeline_module.LLM_MODEL, pipeline_module.LLM_FALLBACK_MODEL]


def test_serves_demo_page_and_sdk(client):
    page = client.get("/").text
    assert "Local Dictation" in page
    assert "Custom words" in page
    assert "LocalDictation" in client.get("/sdk.js").text


class SlowPipeline(FakePipeline):
    def transcribe(self, pcm, language, prompt, hotwords=None):
        import time as _t
        _t.sleep(0.4 * len(pcm) / 32000)
        return super().transcribe(pcm, language, prompt, hotwords)


def test_live_preview_does_not_delay_result():
    import time as _t
    c = TestClient(create_app(SlowPipeline(), allowed_origins=set(), partials=True))
    with c.websocket_connect("/v1/dictate") as ws:
        ws.send_json({"type": "init", "cleanup": False})
        for _ in range(8 * 25):
            ws.send_bytes(FRAME)
            _t.sleep(0.04)
        t0 = _t.monotonic()
        ws.send_json({"type": "commit"})
        while True:
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            if msg["type"] == "result":
                break
        waited = _t.monotonic() - t0
    assert msg["audio_seconds"] == 8.0
    assert waited < 5.5, f"result took {waited:.1f}s after commit (preview backlog)"
