@echo off
rem dev_run.ps1 을 실행 정책과 무관하게 실행한다. 인자는 그대로 전달 (예: dev_run.bat -NoLaunch)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_run.ps1" %*
