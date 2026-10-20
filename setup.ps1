# One-time setup for Scribe. Run from the Scribe folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = $null
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3"), @("python"))) {
    try {
        $ok = & $candidate[0] @($candidate | Select-Object -Skip 1) -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($ok -eq "True") { $python = $candidate; break }
    } catch { }
}
if (-not $python) {
    Write-Host "Python 3.10 or newer was not found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then run this again."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment ..."
    & $python[0] @($python | Select-Object -Skip 1) -m venv .venv
}

Write-Host "Installing dependencies ..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies failed. Check the messages above." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host ""
    Write-Host "Created .env. Add your Deepgram and Anthropic API keys to it now." -ForegroundColor Yellow
    Start-Process notepad.exe ".env"
}

# Create the shortcut, or repoint one left by a copy of Scribe in another folder.
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Scribe.lnk"
$target = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
if ($link.TargetPath -ne $target) {
    $previous = $link.TargetPath
    $link.TargetPath = $target
    $link.Arguments = "-m scribe"
    $link.WorkingDirectory = $PSScriptRoot
    $link.Save()
    if ($previous) {
        Write-Host "Pointed the Scribe desktop shortcut at this folder (it opened $previous)."
    } else {
        Write-Host "Added a Scribe shortcut to your desktop."
    }
}

Write-Host ""
Write-Host "Setup complete. Start Scribe from the desktop shortcut or by running Scribe.bat." -ForegroundColor Green
