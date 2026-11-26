#!/usr/bin/env bash
# Copy the shared SDK into the extension folder (Chrome can't load files outside it),
# and write the audio worklet as its own file so strict-CSP sites can load it.
set -euo pipefail
cd "$(dirname "$0")/.."
cp sdk/local-dictation.js extension/local-dictation.js
node -e '
const src = require("fs").readFileSync("sdk/local-dictation.js", "utf8");
const m = src.match(/const WORKLET = `([\s\S]*?)`;/);
if (!m) { console.error("WORKLET not found"); process.exit(1); }
require("fs").writeFileSync("extension/pcm16-worklet.js", m[1].trim() + "\n");'
echo "extension/ updated (local-dictation.js, pcm16-worklet.js)"
