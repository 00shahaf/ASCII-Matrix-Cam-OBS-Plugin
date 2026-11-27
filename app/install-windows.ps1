# Adds ASCII Cam to the Start menu and puts a double-clickable icon on the desktop.
# Run it by double-clicking install-windows.bat. Run it again after moving this folder.
# Remove with: install-windows.bat -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'ASCII Cam.lnk'
$startLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'ASCII Cam.lnk'

if ($Uninstall) {
    Remove-Item $desktopLnk, $startLnk -ErrorAction SilentlyContinue
    Write-Host 'Removed ASCII Cam shortcuts.'
    exit 0
}

# Find Python: the py launcher first, then python on PATH (skipping the Microsoft Store stub).
$python = $null
foreach ($cmd in @('py', 'python')) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $exe = & $cmd -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { $python = $exe.Trim(); break }
    }
}
if (-not $python) {
    Write-Host 'Python 3 was not found. Install it from https://www.python.org/downloads/'
    Write-Host '(tick "Add python.exe to PATH"), then run this again.'
    exit 1
}

& $python -c "import PySide6, numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing PySide6 and numpy...'
    & $python -m pip install --user -r (Join-Path $appDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed.'; exit 1 }
}

# pythonw.exe runs the app without a console window.
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in @($desktopLnk, $startLnk)) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + (Join-Path $appDir 'ascii_cam.py') + '"'
    $s.WorkingDirectory = $appDir
    $s.IconLocation = (Join-Path $appDir 'icon.ico') + ',0'
    $s.Description = 'Your webcam as live ASCII art or digital rain'
    $s.Save()
}
Write-Host 'Installed. Double-click "ASCII Cam" on your desktop, or find it in the Start menu.'
