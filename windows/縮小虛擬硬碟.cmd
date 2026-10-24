@echo off
rem Bookclub-edit: shrink the WSL virtual disk (ext4.vhdx).
rem Right-click this file and choose "Run as administrator".
rem This file only starts the PowerShell script with the same name (keep both files together).
chcp 65001 >nul
if not exist "%~dpn0.ps1" (
  rem The message is Chinese text encoded for PowerShell, so this file stays plain ASCII.
  powershell.exe -NoProfile -EncodedCommand VwByAGkAdABlAC0ASABvAHMAdAAgACcAfmINTjBSDDAufg9cW4bsZGx4n3guAHAAcwAxAA0wAjDLiopiDDAufg9cW4bsZGx4n3guAGMAbQBkAA0wjFQMMC5+D1xbhuxkbHifeC4AcABzADEADTA+ZShXDFQATgtQx4yZZT5ZDP8WYihX5V13UeZdC07SiQww5V13US2KmlsNMI1RCWMATiFrDDCKYg4wLn4PXFuG7GRseJ94DzA+ZTBSTGhilw0wAjDLigdcL5OkigqKb2AakOV3i5V8dgWAAjAnACAALQBGAG8AcgBlAGcAcgBvAHUAbgBkAEMAbwBsAG8AcgAgAFkAZQBsAGwAbwB3AA==
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1"
if not "%errorlevel%"=="0" pause
