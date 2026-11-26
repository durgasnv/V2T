"""End-to-end smoke test: stream tests/sample.wav to the RUNNING server, the way the browser does.

Usage:  python tests/smoke_real.py [--no-cleanup] [--messy]
Pass:   sample.wav → raw transcript contains "quick brown fox".
        --messy uses messy.wav (fillers + a self-correction) and just prints raw vs cleaned.
"""
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import websockets   # installed with uvicorn[standard]

URL = "ws://127.0.0.1:8765/v1/dictate"
FRAME_SAMPLES = 640


async def main(cleanup: bool, messy: bool) -> int:
    with wave.open(str(Path(__file__).with_name("messy.wav" if messy else "sample.wav"))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), "need 16 kHz mono PCM16"
        pcm = w.readframes(w.getnframes())

    async with websockets.connect(URL, origin="http://127.0.0.1:8765") as ws:
        await ws.send(json.dumps({"type": "init", "language": "en", "cleanup": cleanup}))
        await ws.send(json.dumps({"type": "context", "site": "smoke-test", "before_text": ""}))
        step = FRAME_SAMPLES * 2
        for i in range(0, len(pcm), step):            # 40 ms frames, like the AudioWorklet
            await ws.send(pcm[i:i + step])
        t0 = time.monotonic()
        await ws.send(json.dumps({"type": "commit"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "result":
                break
    print(json.dumps(msg, indent=2))
    print(f"round trip after commit: {int((time.monotonic() - t0) * 1000)} ms")
    if messy:
        print(f"RAW:     {msg['raw_text']}\nCLEANED: {msg['text']}")
        return 0
    ok = "quick brown fox" in msg["raw_text"].lower()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(cleanup="--no-cleanup" not in sys.argv, messy="--messy" in sys.argv)))
