# Diagnose the yt-dlp -> ffmpeg live pipe using a locally served rolling HLS stream.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$hlsDir = Join-Path $root 'testdata\hls'
New-Item -ItemType Directory -Force -Path $hlsDir | Out-Null
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY
$ff = Join-Path $root 'tools\ffmpeg\bin\ffmpeg.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'
$mp3 = Join-Path $root 'testdata\ja_test.mp3'
$manifest = Join-Path $hlsDir 'live.m3u8'

# kill leftovers from earlier runs (matched by command line, not by name)
Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" |
  Where-Object { $_.CommandLine -like '*live.m3u8*' } |
  ForEach-Object { "killing leftover ffmpeg pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*http.server*8765*' } |
  ForEach-Object { "killing leftover http.server pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Get-ChildItem $hlsDir -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

$encoder = Start-Process -FilePath $ff -PassThru -WindowStyle Hidden -ArgumentList @(
  '-hide_banner', '-loglevel', 'error', '-re', '-stream_loop', '-1', '-i', $mp3,
  '-c:a', 'aac', '-b:a', '64k', '-f', 'hls', '-hls_time', '4', '-hls_list_size', '6',
  '-hls_flags', 'delete_segments+omit_endlist', $manifest)
$server = Start-Process -FilePath $py -PassThru -WindowStyle Hidden -ArgumentList @(
  '-m', 'http.server', '8765', '--directory', $hlsDir)

try {
  Start-Sleep -Seconds 10
  "manifest:"; Get-Content $manifest | Select-Object -First 6

  $dump = Join-Path $root 'testdata\ytdlp_dump.bin'
  Remove-Item $dump -ErrorAction SilentlyContinue
  "== yt-dlp -> file (10s cap) =="
  $job = Start-Process -FilePath $py -PassThru -WindowStyle Hidden -RedirectStandardOutput $dump `
    -RedirectStandardError (Join-Path $root 'testdata\ytdlp_err.txt') -ArgumentList @(
      '-m', 'yt_dlp', '--quiet', '--no-warnings', '--no-progress', '--no-playlist', '--no-part',
      '--no-cache-dir', '--ffmpeg-location', (Join-Path $root 'tools\ffmpeg\bin'),
      '-f', 'bestaudio/best', '-o', '-', 'http://127.0.0.1:8765/live.m3u8')
  Start-Sleep -Seconds 10
  if (-not $job.HasExited) { Stop-Process -Id $job.Id -Force -ErrorAction SilentlyContinue }
  if (Test-Path $dump) { "yt-dlp dumped bytes: $((Get-Item $dump).Length)" } else { "yt-dlp dumped nothing" }
  if (Test-Path (Join-Path $root 'testdata\ytdlp_err.txt')) { "yt-dlp stderr:"; Get-Content (Join-Path $root 'testdata\ytdlp_err.txt') | Select-Object -Last 4 }

  "== ffmpeg decode of that dump =="
  $pcm = Join-Path $root 'testdata\pcm.raw'
  Remove-Item $pcm -ErrorAction SilentlyContinue
  & $ff -hide_banner -loglevel 'info' -i $dump -vn -f s16le -ac 1 -ar 16000 $pcm 2>&1 | Select-Object -Last 8
  if (Test-Path $pcm) {
    $bytes = (Get-Item $pcm).Length
    "pcm bytes: $bytes  ->  $([math]::Round($bytes / 32000.0, 2)) seconds of 16k mono audio"
  } else { "no pcm produced" }
} finally {
  foreach ($p in @($encoder, $server)) {
    if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
  }
}
