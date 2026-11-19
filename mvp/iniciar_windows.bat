@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Arquivo .env criado. Preencha os tres segredos antes de continuar.
  notepad .env
  echo Salve o arquivo e feche o Bloco de Notas.
  pause
)
python app.py
pause
