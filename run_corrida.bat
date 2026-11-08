
@echo off
REM Vigilante de la corrida larga de AuditBot.
REM Uso: run_corrida.bat logs\corrida --pausa 3 --tope 55
REM
REM - Reinicia el worker si se cae (maximo 20 caidas).
REM - Espera 30 segundos antes de reiniciar tras una caida.
REM - Reintenta error y sin_decision hasta 3 pasadas adicionales.
REM - No reintenta si se usa --max o si existe STOP.
REM - No reintenta si se alcanza el presupuesto.
REM - Las tareas tienen un maximo de 4 intentos en worker.py.
REM
REM Para detener manualmente:
REM crear un archivo STOP dentro de la carpeta de la bitacora.

setlocal EnableExtensions

for /f "tokens=1,*" %%a in ("%*") do (
    set "DIR=%%a"
    set "RESTO=%%b"
)

if not defined DIR set "DIR=logs\corrida"

set "CAIDAS=0"
set "VUELTA=0"

if not exist "%DIR%" mkdir "%DIR%"

echo.
echo ==========================================
echo        AUDITBOT - CORRIDA UCI
echo ==========================================
echo Bitacora: %DIR%
echo Argumentos: %RESTO%
echo ==========================================
echo.

:inicio

if exist "%DIR%\STOP" goto fin

python worker.py --dir "%DIR%" %RESTO%
set "CODIGO=%ERRORLEVEL%"

if not "%CODIGO%"=="0" goto caida

goto terminado


:caida

set /a CAIDAS+=1

echo.
echo [vigilante] El worker termino con codigo %CODIGO%.
echo [vigilante] Caida %CAIDAS% de 20.

if %CAIDAS% GEQ 20 goto demasiadas

echo [vigilante] Reinicio en 30 segundos...
timeout /t 30 /nobreak >nul

goto inicio

:terminado

if exist "%DIR%\STOP" goto fin

REM No hacer pasadas adicionales en pruebas limitadas con --max.
echo %RESTO% | findstr /c:"--max" >nul
if not errorlevel 1 goto fin

REM Si el worker paro por limite de horas, no se hacen pasadas adicionales.
powershell -NoProfile -Command "if ((Get-Content '%DIR%\eventos.jsonl' -Tail 2 -ErrorAction SilentlyContinue) -match 'limite_horas') { exit 1 } else { exit 0 }"
if errorlevel 1 goto fin

REM Si se alcanzo el presupuesto, no iniciar mas reintentos.
python verificar_bitacora.py --hay-errores "%DIR%"
if errorlevel 2 goto error_verificador
if errorlevel 1 goto reintentar

goto fin


:reintentar

set /a VUELTA+=1

if %VUELTA% GEQ 4 goto agotadas

echo.
echo [vigilante] Quedan tareas pendientes.
echo [vigilante] Espera de 5 minutos.
echo [vigilante] Pasada adicional %VUELTA% de 3.
echo.

timeout /t 300 /nobreak >nul

if exist "%DIR%\STOP" goto fin

goto inicio


:agotadas

echo.
echo [vigilante] Se agotaron las 3 pasadas adicionales.
echo [vigilante] Revisa las tareas pendientes en la bitacora.
goto fin


:error_verificador

echo.
echo [vigilante] Error al ejecutar verificar_bitacora.py.
echo [vigilante] No se iniciaran mas reintentos.
goto fin


:demasiadas

echo.
echo [vigilante] 20 caidas consecutivas.
echo [vigilante] Me detengo para evitar un ciclo infinito.
echo [vigilante] Revisa la bitacora.


:fin

echo.
echo ==========================================
echo       VIGILANTE FINALIZADO
echo ==========================================
echo Bitacora: %DIR%
echo Caidas: %CAIDAS%
echo Pasadas adicionales: %VUELTA%
echo.

endlocal
