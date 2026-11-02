@echo off
setlocal
cd /d "%~dp0"
echo.
echo === Configurar IA Visual - Cime dos Mundos 5.0 ===
echo A chave sera digitada em campo oculto e salva somente no .env local.
echo Ela nao sera enviada ao GitHub por este script.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_IA.ps1"
if errorlevel 1 (
  echo.
  echo Nao foi possivel configurar a chave.
) else (
  echo.
  echo Configuracao local concluida. Reinicie o Cime para carregar a chave.
)
pause
