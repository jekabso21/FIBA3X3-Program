@echo off
REM Build FIBA3X3.exe (double-click to run in a terminal window).
REM Requires Python on PATH. Run this file once to (re)build the .exe.

echo Installing dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo Building FIBA3X3.exe ...
python -m PyInstaller --onefile --console --name FIBA3X3 --clean --noconfirm main.py
if errorlevel 1 goto :error

REM Put a config.json next to the .exe so it is ready to edit and run.
if not exist "dist\config.json" copy "config.json" "dist\config.json" >nul

echo.
echo ============================================================
echo  Done. Your program is: dist\FIBA3X3.exe
echo  Keep config.json in the SAME folder as FIBA3X3.exe.
echo  Double-click FIBA3X3.exe to run.
echo ============================================================
pause
exit /b 0

:error
echo.
echo Build FAILED. See the messages above.
pause
exit /b 1
