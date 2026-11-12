@echo off
rem Run for the Windows Task Scheduler
cd /d "%~dp0"
rem new letters first (application statuses for the report), then the vacancy search
".venv\Scripts\python.exe" applications.py >> last_run.log 2>&1
rem the same run: do not rotate errors.log (applications.py started it)
set JOBHUNTER_CONTINUE_RUN=1
".venv\Scripts\python.exe" main.py >> last_run.log 2>&1
