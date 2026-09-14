# livecap setup - idempotent installer
# Creates .venv (python 3.12 if uv is available, else system python),
# installs python deps, downloads a static ffmpeg, prefetches models.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root
$log = Join-Path $root 'setup.log'

function Say([string]$m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Write-Host $line
  try { Add-Content -LiteralPath $log -Value $line -Encoding utf8 } catch {}
}

function Run([string]$exe, [string[]]$arguments) {
  Say ("run: {0} {1}" -f $exe, ($arguments -join ' '))
  & $exe @arguments
  if ($LASTEXITCODE -ne 0) { throw ("command failed ({0}): {1}" -f $LASTEXITCODE, $exe) }
}

Say "project root: $root"

# --- disk space -------------------------------------------------------------
try {
  Get-PSDrive -PSProvider FileSystem -ErrorAction Stop |
    Where-Object { $null -ne $_.Free -and $_.Free -gt 0 } |
    ForEach-Object { Say ("disk {0}: free {1:N1} GB" -f $_.Name, ($_.Free / 1GB)) }
} catch { Say "disk info unavailable: $($_.Exception.Message)" }

# --- report only presence of API keys, never their value --------------------
foreach ($k in 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'AZURE_SPEECH_KEY', 'IFLYTEK_APPID') {
  $has = [bool](Get-Item -Path "env:$k" -ErrorAction SilentlyContinue)
  Say "env $k present: $has"
}

# --- venv -------------------------------------------------------------------
$venv   = Join-Path $root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$uv     = Get-Command uv -ErrorAction SilentlyContinue
Say ("uv: " + $(if ($uv) { $uv.Source } else { 'not found' }))

function New-Venv([string]$pyver) {
  if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
  if ($uv -and $pyver) {
    Run $uv.Source @('venv', '--python', $pyver, '--seed', $venv)
  } else {
    Run 'python' @('-m', 'venv', $venv)
  }
}

# uv-created venvs ship without pip, so drive installs through uv when available
function Pip-Install([string[]]$arguments) {
  if ($uv) {
    Run $uv.Source (@('pip', 'install', '--python', $venvPy) + $arguments)
  } else {
    Run $venvPy (@('-m', 'pip', 'install') + $arguments)
  }
}

if (-not (Test-Path -LiteralPath $venvPy)) {
  if ($uv) { New-Venv '3.12' } else { New-Venv $null }
}
Run $venvPy @('--version')

# --- python deps ------------------------------------------------------------
$pkgs = @('yt-dlp', 'faster-whisper', 'onnxruntime', 'huggingface_hub', 'numpy',
          'requests', 'PySide6', 'nvidia-cudnn-cu12', 'nvidia-cublas-cu12')

function Install-Core {
  Pip-Install (@('--upgrade') + $pkgs)
}

try {
  Install-Core
} catch {
  Say "core install failed: $($_.Exception.Message)"
  if ($uv) { Say 'retrying with python 3.12'; New-Venv '3.12'; Install-Core } else { throw }
}
Say 'core deps installed'

# --- translation deps (local ja->zh). Optional: LLM translator needs none. ---
try {
  Pip-Install @('--upgrade', 'torch', '--index-url', 'https://download.pytorch.org/whl/cpu')
  Pip-Install @('--upgrade', 'transformers', 'sentencepiece', 'sacremoses')
  Say 'translation deps installed'
} catch {
  Say "translation deps failed, LLM translator still usable: $($_.Exception.Message)"
}

# --- ffmpeg -----------------------------------------------------------------
$tools = Join-Path $root 'tools'
$ffdir = Join-Path $tools 'ffmpeg'
$ffexe = Join-Path $ffdir 'bin\ffmpeg.exe'
if (-not (Test-Path -LiteralPath $ffexe)) {
  New-Item -ItemType Directory -Force -Path $tools | Out-Null
  $zip = Join-Path $tools 'ffmpeg.zip'
  Say 'downloading ffmpeg (gyan.dev essentials build)'
  Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip -TimeoutSec 900
  Say 'extracting ffmpeg'
  Expand-Archive -LiteralPath $zip -DestinationPath $tools -Force
  $inner = Get-ChildItem -LiteralPath $tools -Directory |
    Where-Object { $_.Name -like 'ffmpeg-*' } | Select-Object -First 1
  if (-not $inner) { throw 'ffmpeg archive layout unexpected' }
  if (Test-Path -LiteralPath $ffdir) { Remove-Item -LiteralPath $ffdir -Recurse -Force }
  Move-Item -LiteralPath $inner.FullName -Destination $ffdir
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
}
Say ("ffmpeg: " + (& $ffexe -version | Select-Object -First 1))

# --- models -----------------------------------------------------------------
$prefetch = Join-Path $root 'scripts\prefetch_models.py'

Say 'prefetching models (large-v3 ~3GB, first run takes a while)'
& $venvPy $prefetch
if ($LASTEXITCODE -ne 0) {
  Say 'retrying model download via hf-mirror.com'
  $env:HF_ENDPOINT = 'https://hf-mirror.com'
  & $venvPy $prefetch
}

Say 'SETUP DONE'
