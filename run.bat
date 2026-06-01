@echo off
REM ERP-CRM Data Integrity Engine — Full Stack Launcher
REM Starts: CRM API, ERP API, ML Scorer, Streamlit Dashboard

echo ========================================
echo  ERP-CRM Data Integrity Engine
echo  Full Stack (no Docker)
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 ( echo [ERROR] Python not found & pause & exit /b 1 )

echo [1/5] Installing dependencies...
pip install -r requirements.txt --quiet
echo       Done.

if not exist "data\sync_engine.db" (
    echo [2/5] Generating synthetic dataset...
    cd data_generator & python generate_data.py & cd ..
) else (
    echo [2/5] Dataset exists, skipping generation.
)

echo [3/5] Training ML quality scorer...
cd ml_scorer
if not exist "models\quality_scorer.pkl" (
    set DB_PATH=..\data\sync_engine.db
    python train_scorer.py
) else (
    echo       Model already trained, skipping.
)
cd ..

echo [4/5] Starting APIs...
start "CRM API (5001)"    cmd /k "set DB_PATH=data/sync_engine.db && set CRM_PORT=5001    && cd crm_api    && python app.py"
timeout /t 2 /nobreak >nul
start "ERP API (5002)"    cmd /k "set DB_PATH=data/sync_engine.db && set ERP_PORT=5002    && cd erp_api    && python app.py"
timeout /t 2 /nobreak >nul
start "ML Scorer (5003)"  cmd /k "set DB_PATH=data/sync_engine.db && set SCORER_PORT=5003 && cd ml_scorer  && python scorer_api.py"
timeout /t 3 /nobreak >nul

echo [5/5] Starting Streamlit Dashboard...
start "Dashboard (8501)"  cmd /k "set DB_PATH=data/sync_engine.db && cd dashboard && streamlit run app.py --server.port 8501"

echo.
echo ========================================
echo  All services starting...
echo.
echo  CRM API   : http://localhost:5001
echo  ERP API   : http://localhost:5002
echo  ML Scorer : http://localhost:5003
echo  Dashboard : http://localhost:8501
echo.
echo  To run sync pipeline manually:
echo    python sync_bridge.py --all
echo.
echo  To use n8n (requires Docker):
echo    docker-compose up n8n
echo    Import: n8n_workflows/sync_workflow.json
echo ========================================
echo.
echo Opening dashboard in browser...
timeout /t 5 /nobreak >nul
start http://localhost:8501
pause
