# SDD ledger — plan: /mnt/c/Users/durga/desktop/voice/prototype/docs/plan.md
Ruling: no git repo — skip worktree/task-start/task-done scripts and commits; ledger tracked here by hand — user never asked for git — cost if wrong: no per-task diff history.
Pre-flight: Task 3 consumes Task 1's /sdk.js route + result message shape; Task 4 consumes Task 3's SDK file. Consistent.
Task 1: Ruling: implementation written before the first RED run (pip install took ~20 min) — compensated with mutation checks: 4 deliberate bugs (short-audio guard, origin check, cleanup fallback, cancel) each turned exactly 1 test red — cost if wrong: none observed.
Task 1: Ruling: tests hung (not failed) on 2 mutations — added "error message fails the test" to run_session and a commit-probe to the origin test — cost if wrong: none.
Task 1: complete (tests: .venv/bin/python -m pytest tests -q → 7 passed)
Task 2: Ruling: HF "xet" downloader stalled at 0 bytes — documented HF_HUB_DISABLE_XET=1 in README — cost if wrong: none (plain HTTP download).
Task 2: complete (tests/smoke_real.py → PASS, raw "The quick brown fox jumps over the lazy dog."; warm ~2.0 s ASR + ~0.9 s cleanup)
Task 2: Ruling: kept beam_size=5 (greedy was not measurably faster on this CPU); added a few-shot example + temperature 0 to the cleanup prompt — the 1.5B model kept fillers / turned questions into commands without it — cost if wrong: ~100 extra prompt tokens.
Task 3: Ruling: added data-local-dictation marker so page + extension copies don't both type (found in self-review).
Task 3: Ruling: extension loads the worklet from its own file (web_accessible_resources) — strict-CSP sites block blob: worklets.
Task 3: Bug found by browser e2e: blocking live preview made frames pile up so results never arrived → previews now run in background on the last 4 s only. RED: test_live_preview_does_not_delay_result hung on old code; GREEN after fix.
Task 3: complete (pytest tests -q → 8 passed; tests/browser_e2e.mjs → 8/8 checks; unreachable-server shows error)
Task 4: complete (extension verified in headless Chromium on 127.0.0.1:8000: textarea + contenteditable)
Final review: self-review (no subagent tool used — not requested by user)
Final: minor (deferred): 1.5B cleanup model occasionally changes a pronoun ("her" → "me"); larger model (qwen2.5:3b) would fix at ~2x cleanup time.
Final: minor (deferred): content-script mic permission is asked once per website (offscreen-document mic would make it one prompt).
Improvements: Ruling: preferred cleanup model is qwen2.5:3b with automatic 404 fallback to the already-installed qwen2.5:1.5b — improves pronouns without breaking the current demo — cost if wrong: one failed HTTP call when 3b is missing.
Improvements: Ruling: added dictionary/hotwords, style/editing, exact replacements/snippets, richer context, language auto-detect, ASR device/compute config and optional SHARED_SECRET — all protocol-compatible extras — cost if wrong: slightly larger init/context messages.
Improvements: Ruling: live previews now wait for 1 s of new audio instead of re-running as soon as the previous preview finishes — reduces CPU contention before commit — cost if wrong: preview updates are ~1 s coarser.
Improvements: Bug found by browser e2e: the 1.5b fallback changed "Tuesday" to "Thursday" and invented "PM" when the richer prompt was added → added an explicit "never change weekdays/dates/times/numbers/names/pronouns, never add AM/PM" guard and "only edit Dictated speech". RED: 7/8 e2e; GREEN after fix (8/8 twice).
Improvements: complete (tests: .venv/bin/python -m pytest tests -q → 16 passed).
