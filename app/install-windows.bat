@echo off
rem Double-click to add an "ASCII Cam" icon to your desktop and Start menu.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
pause
