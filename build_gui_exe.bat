@echo off
REM Build FIBA3X3-GUI.exe (double-click to open the app window).
REM Requires Python on PATH. Run this file once to (re)build the .exe.

echo Installing dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo Building FIBA3X3-GUI.exe ...
python -m PyInstaller --onefile --windowed --name FIBA3X3-GUI --clean --noconfirm gui.py
if errorlevel 1 goto :error

if not exist "dist\config.json" copy "config.json" "dist\config.json" >nul

echo.
echo ============================================================
echo  Done. Your app is: dist\FIBA3X3-GUI.exe
echo  Keep config.json in the SAME folder as the .exe.
echo  Double-click FIBA3X3-GUI.exe to run.
echo ============================================================
pause
exit /b 0

:error
echo.
echo Build FAILED. See the messages above.
pause
exit /b 1
