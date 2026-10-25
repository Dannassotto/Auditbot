
@echo off
setlocal
cd /d "%~dp0"
title AudiBot

echo.
echo ========================================
echo          INICIANDO AUDIBOT
echo ========================================
echo.

REM ---- Comprobar requisitos ----
where python >nul 2>&1
if errorlevel 1 goto no_python

where npm >nul 2>&1
if errorlevel 1 goto no_node

REM ---- Configurar archivo .env ----
if exist ".env" goto revisar_python

if not exist ".env.example" goto no_env_example

copy ".env.example" ".env" >nul
if errorlevel 1 goto error_env

echo.
echo Se creo el archivo .env.
echo Configura tu API_KEY, guarda los cambios y cierra el Bloc de notas.
notepad ".env"
echo.
echo Cuando hayas guardado el archivo, continua.
pause

:revisar_python
REM ---- Instalar dependencias de Python ----
echo.
echo Revisando dependencias de Python...
echo La primera vez puede tardar varios minutos.

python -m pip install -q -r requirements.txt vosk sounddevice edge-tts
if errorlevel 1 goto error_python

REM ---- Crear base de datos si no existe ----
if exist "data\auditbot.db" goto revisar_electron

echo.
echo Creando la base de datos...
python db\seed.py
if errorlevel 1 goto error_db

REM ---- Instalar dependencias de Electron ----
:revisar_electron
if exist "electron\node_modules" goto iniciar_servidor

echo.
echo Instalando dependencias de Electron...
pushd "%~dp0electron"
call npm install
if errorlevel 1 (
    popd
    goto error_npm
)
popd

REM ---- Iniciar servidor web ----
:iniciar_servidor
echo.
echo Iniciando servidor web de AudiBot...

REM Evitar abrir una segunda ventana del navegador desde Python.
start "AudiBot - Servidor" /min cmd /c "cd /d ""%~dp0"" && set AUDIBOT_NO_BROWSER=1 && python servidor.py"

REM ---- Esperar a que el servidor responda ----
echo.
echo Esperando a que el servidor este disponible en el puerto 5000...

set "SERVIDOR_LISTO="

for /L %%i in (1,1,30) do (
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5000/api/estado' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set "SERVIDOR_LISTO=1"
        goto servidor_listo
    )
    timeout /t 1 /nobreak >nul
)

echo.
echo ERROR: El servidor no respondio en el puerto 5000.
echo.
echo Comprueba la ventana del servidor y los errores de Python.
echo Verifica que el puerto 5000 sea el configurado en servidor.py.
echo No se iniciara Electron porque el servidor no esta listo.
pause
goto error_final

:servidor_listo
echo.
echo Servidor disponible correctamente.

REM ---- Comprobar modelo de reconocimiento de voz ----
if exist "%~dp0model" goto abrir_chat
if exist "%~dp0model-grande" goto abrir_chat

echo.
echo AVISO: No se encontro la carpeta model ni model-grande.
echo El chat puede funcionar, pero el reconocimiento de voz
echo de escritorio podria no estar disponible.

REM ---- Abrir chat en navegador ----
:abrir_chat
echo.
echo Abriendo el chat de AudiBot en el navegador...
start "" "http://127.0.0.1:5000/"

REM ---- Iniciar aplicacion de escritorio ----
echo.
echo Iniciando la aplicacion de escritorio...
pushd "%~dp0electron"
call npx electron .
set "CODIGO_ELECTRON=%ERRORLEVEL%"
popd

echo.
echo AudiBot se ha cerrado.
echo El servidor puede seguir ejecutandose en segundo plano.
echo Para detenerlo, cierra el proceso de Python que inicio este archivo.
echo.
pause

endlocal & exit /b %CODIGO_ELECTRON%

REM ========================================
REM              MANEJO DE ERRORES
REM ========================================

:no_python
echo ERROR: Python no esta instalado o no esta en PATH.
echo Instala Python desde python.org y marca Add Python to PATH.
goto error_final

:no_node
echo ERROR: Node.js y npm son necesarios.
echo Instala Node.js desde nodejs.org.
goto error_final

:no_env_example
echo ERROR: No se encuentra el archivo .env.example.
echo Comprueba que este archivo este en la carpeta principal del proyecto.
goto error_final

:error_env
echo ERROR: No se pudo crear el archivo .env.
goto error_final

:error_python
echo ERROR: No se pudieron instalar las dependencias de Python.
goto error_final

:error_db
echo ERROR: No se pudo crear la base de datos.
goto error_final

:error_npm
echo ERROR: No se pudieron instalar las dependencias de Electron.
goto error_final

:error_final
echo.
pause
endlocal
exit /b 1
