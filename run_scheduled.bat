@echo off
cd /d "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge"
echo RUNNING > logs\scheduled_run_status.txt
C:\Python313\python.exe main.py > logs\scheduled_run_output.log 2>&1
echo DONE_%ERRORLEVEL% > logs\scheduled_run_status.txt
