# Build livecap.exe (windowed GUI) + livecap-cli.exe (console) into dist\livecap
# NOTE: keep ErrorActionPreference at Continue - uv/pyinstaller write progress to
# stderr, which Windows PowerShell would otherwise turn into a terminating error.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$py = Join-Path $root '.venv\Scripts\python.exe'
$dist = Join-Path $root 'dist\livecap'

# uv is optional: it is only used to install pyinstaller quickly
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
  foreach ($candidate in @("$env:USERPROFILE\.local\bin\uv.exe",
                           "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe")) {
    if (Test-Path $candidate) { $uv = $candidate; break }
  }
}

function Fail([string]$message) { Write-Host "ERROR: $message"; exit 1 }

Write-Host '== icon =='
& $py packaging\make_icon.py packaging\app.ico

Write-Host '== pyinstaller =='
if ($uv -and (Test-Path $uv)) {
  & $uv pip install --python $py pyinstaller 2>&1 | Select-Object -Last 3
} else {
  & $py -m pip install --upgrade pyinstaller 2>&1 | Select-Object -Last 3
}
if ($LASTEXITCODE -ne 0) { Fail "could not install pyinstaller ($LASTEXITCODE)" }

Write-Host '== build =='
& (Join-Path $root '.venv\Scripts\pyinstaller.exe') `
  --noconfirm --clean `
  --distpath (Join-Path $root 'dist') `
  --workpath (Join-Path $root 'build') `
  (Join-Path $root 'packaging\livecap.spec') 2>&1 | Select-Object -Last 12
if ($LASTEXITCODE -ne 0) { Fail "pyinstaller failed ($LASTEXITCODE)" }

Write-Host '== copy runtime assets next to the exe =='
if (-not (Test-Path $dist)) { Fail "expected $dist" }
Copy-Item (Join-Path $root 'models') -Destination $dist -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $dist 'tools') | Out-Null
Copy-Item (Join-Path $root 'tools\ffmpeg') -Destination (Join-Path $dist 'tools') -Recurse -Force
Copy-Item (Join-Path $root 'README.md') -Destination $dist -Force

Write-Host '== installer (Inno Setup) =='
$iscc = @(
  (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
  (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
  (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($iscc) {
  & $iscc (Join-Path $root 'packaging\installer.iss') 2>&1 | Select-Object -Last 6
  if ($LASTEXITCODE -ne 0) { Fail "installer build failed ($LASTEXITCODE)" }
  Get-ChildItem (Join-Path $root 'dist\installer') -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ("setup exe: {0} ({1:N0} MB)" -f $_.Name, ($_.Length / 1MB)) }
} else {
  Write-Host 'Inno Setup not found - skipping the installer'
  Write-Host '  install it with: winget install JRSoftware.InnoSetup'
}

Write-Host '== result =='
Get-ChildItem $dist | Select-Object Name, @{n = 'MB'; e = {
    if ($_.PSIsContainer) { [math]::Round(((Get-ChildItem $_.FullName -Recurse -File |
      Measure-Object Length -Sum).Sum) / 1MB, 1) } else { [math]::Round($_.Length / 1MB, 2) } } }
$total = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("total: {0:N0} MB" -f ($total / 1MB))
exit 0
