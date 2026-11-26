# Creates a 16 kHz mono WAV using Windows' built-in text-to-speech (for smoke tests).
# Usage: powershell -File make_sample.ps1 [-Text "..."] [-Name sample.wav]
param(
  [string]$Text = "The quick brown fox jumps over the lazy dog.",
  [string]$Name = "sample.wav"
)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$out = Join-Path $PSScriptRoot $Name
$s.SetOutputToWaveFile($out, $fmt)
$s.Speak($Text)
$s.Dispose()
Write-Output "wrote $out"
