@echo off
REM ============================================
REM AI Employee — Windows Task Scheduler Setup
REM ============================================
REM This script registers a Windows Scheduled Task
REM that runs the AI Employee every 5 minutes.
REM
REM Run this file AS ADMINISTRATOR.
REM ============================================

set TASK_NAME=AI_Employee_Scheduler
set PROJECT_DIR=%~dp0..
set PYTHON_EXE=python
set SCRIPT_PATH=%PROJECT_DIR%\scripts\run_ai_employee.py

echo.
echo ================================================
echo   AI Employee — Task Scheduler Setup
echo ================================================
echo.
echo   Task Name : %TASK_NAME%
echo   Script    : %SCRIPT_PATH%
echo   Interval  : Every 5 minutes
echo.

REM Delete existing task if it exists (ignore errors)
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1

REM Create the scheduled task
schtasks /Create ^
  /TN "%TASK_NAME%" ^
  /TR "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\" --once" ^
  /SC MINUTE ^
  /MO 5 ^
  /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo   [SUCCESS] Task "%TASK_NAME%" created.
    echo   It will run every 5 minutes automatically.
    echo.
    echo   Useful commands:
    echo     View task:    schtasks /Query /TN "%TASK_NAME%" /V
    echo     Run now:      schtasks /Run /TN "%TASK_NAME%"
    echo     Disable:      schtasks /Change /TN "%TASK_NAME%" /DISABLE
    echo     Delete:       schtasks /Delete /TN "%TASK_NAME%" /F
    echo.
) else (
    echo.
    echo   [ERROR] Failed to create task.
    echo   Make sure you are running this as Administrator.
    echo.
)

pause
