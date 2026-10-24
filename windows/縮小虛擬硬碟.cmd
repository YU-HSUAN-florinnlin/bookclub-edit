@echo off
rem Bookclub-edit: shrink the WSL virtual disk (ext4.vhdx).
rem Right-click this file and choose "Run as administrator".
rem This file only starts the PowerShell script with the same name (keep both files together).
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1"
if errorlevel 1 pause
