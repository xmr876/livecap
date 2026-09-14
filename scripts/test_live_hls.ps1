# Serves the test clip as a rolling HLS "live" stream and runs livecap against it.
# This validates the yt-dlp -> ffmpeg -> PCM live path without touching the internet.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $root
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_OFFLINE = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$hlsDir = Join-Path $root 'testdata\hls'
New-Item -ItemType Directory -Force -Path $hlsDir | Out-Null
Get-ChildItem $hlsDir -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

$ff = Join-Path $root 'tools\ffmpeg\bin\ffmpeg.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'
$mp3 = Join-Path $root 'testdata\ja_test.mp3'
$manifest = Join-Path $hlsDir 'live.m3u8'

$ffArgs = @('-hide_banner', '-loglevel', 'error', '-re', '-stream_loop', '-1', '-i', $mp3,
            '-c:a', 'aac', '-b:a', '64k', '-f', 'hls', '-hls_time', '4', '-hls_list_size', '6',
            '-hls_flags', 'delete_segments+omit_endlist', $manifest)
$encoder = Start-Process -FilePath $ff -ArgumentList $ffArgs -PassThru -WindowStyle Hidden
$server = Start-Process -FilePath $py -ArgumentList @('-m', 'http.server', '8765', '--directory', $hlsDir) -PassThru -WindowStyle Hidden

try {
  Start-Sleep -Seconds 8
  $code = (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/live.m3u8' -UseBasicParsing -TimeoutSec 15).StatusCode
  "hls manifest http $code"
  & $py -m livecap.main --url 'http://127.0.0.1:8765/live.m3u8' --no-overlay `
        --max-duration 45 --translator nllb --log (Join-Path $root 'testdata\run_live_hls.jsonl')
  "livecap exit: $LASTEXITCODE"
} finally {
  foreach ($p in @($encoder, $server)) {
    if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
  }
}
