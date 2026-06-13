@echo off
cd /d "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge"
echo RUN START %DATE% %TIME% > run_output.log
C:\Python313\python.exe main.py >> run_output.log 2>&1
echo %ERRORLEVEL% > run_exitcode.txt
echo DONE >> run_output.log
