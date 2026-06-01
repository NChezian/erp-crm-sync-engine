@echo off
REM ERP-CRM Data Integrity Engine — Phase 1 Startup
REM Starts both mock APIs and generates data if needed

echo ========================================
echo  ERP-CRM Data Integrity Engine
echo  Phase 1: Mock APIs
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause & exit /b 1
)

REM Install dependencies
echo [1/4] Installing dependencies...
pip install -r requirements.txt --quiet
echo       Done.

REM Generate data if DB doesn't exist
if not exist "data\sync_engine.db" (
    echo [2/4] Generating synthetic dataset...
    cd data_generator
    python generate_data.py
    cd ..
    echo       Done.
) else (
    echo [2/4] Dataset already exists, skipping generation.
)

REM Start CRM API
echo [3/4] Starting Mock CRM API on port 5001...
start "CRM API" cmd /k "set DB_PATH=data/sync_engine.db && set CRM_PORT=5001 && cd crm_api && python app.py"

timeout /t 2 /nobreak >nul

REM Start ERP API
echo [4/4] Starting Mock ERP API on port 5002...
start "ERP API" cmd /k "set DB_PATH=data/sync_engine.db && set ERP_PORT=5002 && cd erp_api && python app.py"

echo.
echo ========================================
echo  APIs are starting up...
echo.
echo  CRM API : http://localhost:5001
echo  ERP API : http://localhost:5002
echo.
echo  Endpoints:
echo    GET  /health
echo    GET  /api/deals?synced=0^&limit=50   (CRM)
echo    GET  /api/deals/stats               (CRM)
echo    POST /api/orders                    (ERP)
echo    GET  /api/stats                     (ERP)
echo ========================================
echo.
pause
