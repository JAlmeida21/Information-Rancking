@echo off
REM Plantilla para arrancar la API de TopRank.
REM 1. Copia este archivo como  iniciar_api.bat
REM 2. Llena tus credenciales abajo (iniciar_api.bat esta en .gitignore y no se sube)
REM 3. Doble clic en iniciar_api.bat, o escribe  .\iniciar_api.bat  en la terminal.

cd /d "%~dp0"

REM --- Credenciales de NetSuite (Token-Based Authentication) ---
set NS_ACCOUNT=TU_ACCOUNT_ID
set NS_CONSUMER_KEY=TU_CONSUMER_KEY
set NS_CONSUMER_SECRET=TU_CONSUMER_SECRET
set NS_TOKEN_ID=TU_TOKEN_ID
set NS_TOKEN_SECRET=TU_TOKEN_SECRET

REM --- Clave que n8n manda en el header X-API-Key ---
set TOPRANK_API_KEY=TU_API_KEY

REM Ajusta la ruta si tu Python esta en otro lugar (o usa simplemente: python api.py)
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" api.py
pause
