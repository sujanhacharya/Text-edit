@echo off
setlocal EnableExtensions
title InkPatch installer

rem Run from the project directory so every relative path is predictable.
cd /d "%~dp0"

if /i not "%OS%"=="Windows_NT" (
    echo Unsupported operating system: %OS%
    echo This installer supports Windows only. On Linux, BSD, macOS, or Android/Termux run install.sh.
    exit /b 1
)

if /i "%PROCESSOR_ARCHITECTURE%"=="x86" (
    echo Unsupported operating system: 32-bit Windows.
    echo Install 64-bit Windows and a 64-bit Python 3.10+ build.
    exit /b 1
)

if not exist "requirements.txt" (
    echo Error: requirements.txt was not found next to this script.
    exit /b 1
)

echo    ___ ____
echo   ^|_ _^|  _ \
echo    ^| ^|^| ^|_) ^|
echo    ^| ^|^|  __/
echo   ^|___^|_^|
echo InkPatch installer
echo Project: %CD%
echo.

set "DOCKER_AVAILABLE=0"
set "PODMAN_AVAILABLE=0"
where docker >nul 2>&1
if not errorlevel 1 set "DOCKER_AVAILABLE=1"
where podman >nul 2>&1
if not errorlevel 1 set "PODMAN_AVAILABLE=1"

if "%DOCKER_AVAILABLE%%PODMAN_AVAILABLE%"=="00" goto :native_no_container
goto :ask_container

:ask_container
echo A container runtime was detected:
if "%DOCKER_AVAILABLE%"=="1" echo   Docker
if "%PODMAN_AVAILABLE%"=="1" echo   Podman
echo Use Docker/Podman, or use the native OS install?
echo   n. Native OS install (Python virtual environment)
if "%DOCKER_AVAILABLE%"=="1" echo   d. Docker
if "%PODMAN_AVAILABLE%"=="1" echo   p. Podman
echo.

where choice >nul 2>&1
if errorlevel 1 goto :ask_container_text
if "%DOCKER_AVAILABLE%%PODMAN_AVAILABLE%"=="11" goto :ask_container_both
if "%DOCKER_AVAILABLE%"=="1" goto :ask_container_docker
goto :ask_container_podman

:ask_container_both
choice /c DPN /n /m "Choose [d/p/n]: "
if errorlevel 3 goto :native
if errorlevel 2 goto :use_podman
if errorlevel 1 goto :use_docker
goto :ask_container_both

:ask_container_docker
choice /c DN /n /m "Choose [d/n]: "
if errorlevel 2 goto :native
if errorlevel 1 goto :use_docker
goto :ask_container_docker

:ask_container_podman
choice /c PN /n /m "Choose [p/n]: "
if errorlevel 2 goto :native
if errorlevel 1 goto :use_podman
goto :ask_container_podman

:ask_container_text
set "METHOD="
set /p "METHOD=Choose [d/p/n] (default n): "
if not defined METHOD goto :native
if /i "%METHOD%"=="d" if "%DOCKER_AVAILABLE%"=="1" goto :use_docker
if /i "%METHOD%"=="p" if "%PODMAN_AVAILABLE%"=="1" goto :use_podman
if /i "%METHOD%"=="n" goto :native
echo Enter a valid choice.
echo.
goto :ask_container_text

:use_docker
echo.
echo Docker selected. Run these commands:
echo   cd /d "%~dp0"
docker compose version >nul 2>&1
if not errorlevel 1 goto :docker_compose
docker-compose version >nul 2>&1
if not errorlevel 1 goto :docker_compose_legacy
echo   docker build -t inkpatch .
echo   docker run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   docker build --build-arg TESSERACT_LANGS="eng deu chi_sim chi_tra hin kan" --build-arg EXTRA_FONTS="fonts-noto-cjk fonts-noto-core" -t inkpatch .
echo Then run the docker run command above.
goto :container_done

:docker_compose
echo   docker compose up --build -d
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   set "TESSERACT_LANGS=eng deu chi_sim chi_tra hin kan"
echo   set "EXTRA_FONTS=fonts-noto-cjk fonts-noto-core"
echo   docker compose up --build -d
goto :container_done

:docker_compose_legacy
echo   docker-compose up --build -d
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   set "TESSERACT_LANGS=eng deu chi_sim chi_tra hin kan"
echo   set "EXTRA_FONTS=fonts-noto-cjk fonts-noto-core"
echo   docker-compose up --build -d
goto :container_done

:use_podman
echo.
echo Podman selected. Run these commands:
echo   cd /d "%~dp0"
podman-compose version >nul 2>&1
if not errorlevel 1 goto :podman_compose
podman compose version >nul 2>&1
if not errorlevel 1 goto :podman_compose_native
echo   podman build -t inkpatch .
echo   podman run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   podman build --build-arg TESSERACT_LANGS="eng deu chi_sim chi_tra hin kan" --build-arg EXTRA_FONTS="fonts-noto-cjk fonts-noto-core" -t inkpatch .
echo Then run the podman run command above.
goto :container_done

:podman_compose
echo   podman-compose up --build -d
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   set "TESSERACT_LANGS=eng deu chi_sim chi_tra hin kan"
echo   set "EXTRA_FONTS=fonts-noto-cjk fonts-noto-core"
echo   podman-compose up --build -d
goto :container_done

:podman_compose_native
echo   podman compose up --build -d
echo.
echo Optional Tesseract languages and script fonts are build arguments.
echo For German, Chinese, Hindi, and Kannada support, rebuild with:
echo   set "TESSERACT_LANGS=eng deu chi_sim chi_tra hin kan"
echo   set "EXTRA_FONTS=fonts-noto-cjk fonts-noto-core"
echo   podman compose up --build -d
goto :container_done

:container_done
echo.
echo Open http://127.0.0.1:8000 after the container starts.
echo See INSTALL.md for language packs and extra fonts.
echo.
pause
exit /b 0

:native_no_container
echo Docker/Podman was not detected; using the native OS install.
echo To use containers instead, install Docker Desktop or Podman Desktop:
echo   Docker Desktop: https://www.docker.com/products/docker-desktop/
echo   Podman Desktop: https://podman-desktop.io/
echo The installer never installs these or builds containers.
echo.

:native
echo == Python ==
set "PYTHON_CMD="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python3"
)
if not defined PYTHON_CMD goto :missing_python

%PYTHON_CMD% --version
%PYTHON_CMD% -m venv --help >nul 2>&1
if errorlevel 1 goto :missing_venv

echo.
echo == Tesseract ==
set "TESSERACT_STATE=missing"
set "TESSERACT_EXE="
where tesseract >nul 2>&1
if not errorlevel 1 set "TESSERACT_EXE=tesseract"
if defined TESSERACT_EXE goto :check_tesseract_langs
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESSERACT_EXE=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
if not defined TESSERACT_EXE goto :tesseract_missing

:check_tesseract_langs
"%TESSERACT_EXE%" --version >nul 2>&1
if errorlevel 1 goto :tesseract_missing
"%TESSERACT_EXE%" --list-langs 2>&1 | findstr /x /c:"eng" >nul
if errorlevel 1 goto :tesseract_no_eng
set "TESSERACT_STATE=ok"
echo Tesseract: %TESSERACT_EXE% (English data available)
goto :after_tesseract

:tesseract_missing
set "TESSERACT_STATE=missing"
echo Tesseract: not installed
goto :after_tesseract

:tesseract_no_eng
set "TESSERACT_STATE=no-eng"
echo Tesseract: installed, but English data (eng) is missing
goto :after_tesseract

:after_tesseract
echo.
echo == Python dependencies ==
if not exist ".venv\Scripts\python.exe" goto :install_python_deps
".venv\Scripts\python.exe" -c "import importlib.util as u, fastapi, uvicorn, pydantic, PIL, numpy, cv2; raise SystemExit(0 if (u.find_spec('multipart') or u.find_spec('python_multipart')) else 1)" >nul 2>&1
if errorlevel 1 goto :install_python_deps
".venv\Scripts\python.exe" -m pip check >nul 2>&1
if errorlevel 1 goto :install_python_deps
echo Python dependencies are already installed.
goto :optional_check

:install_python_deps
echo Python dependencies are not installed in .venv, or the environment is incomplete.
echo Installing with:
echo   %PYTHON_CMD% -m venv .venv
echo   .venv\Scripts\activate
echo   python -m pip install -r requirements.txt
echo.
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :venv_error
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :venv_error
python -m pip install -r requirements.txt
if errorlevel 1 goto :pip_error
".venv\Scripts\python.exe" -c "import importlib.util as u, fastapi, uvicorn, pydantic, PIL, numpy, cv2; raise SystemExit(0 if (u.find_spec('multipart') or u.find_spec('python_multipart')) else 1)" >nul 2>&1
if errorlevel 1 goto :import_error
echo Python dependencies are installed.
goto :optional_check

:missing_python
echo.
echo Python 3.10 or newer is not installed, or it is not on PATH.
echo Install it from:
echo   https://www.python.org/downloads/windows/
echo Tick "Add python.exe to PATH" in the installer.
echo Or use WinGet:
echo   winget install -e --id Python.Python.3.13
echo Then close and reopen this window and run install.bat again.
echo.
pause
exit /b 1

:missing_venv
echo.
echo Python is installed, but its venv module could not be loaded.
echo Reinstall Python 3.10+ and make sure the standard library is included.
echo   winget install -e --id Python.Python.3.13
echo.
pause
exit /b 1

:venv_error
echo.
echo Could not create .venv.
echo Check that Python 3.10+ is installed and that this folder is writable.
echo If .venv is broken, delete it and run install.bat again.
echo.
pause
exit /b 1

:pip_error
echo.
echo Python dependency installation failed.
echo Check your internet connection or proxy, then run:
echo   .venv\Scripts\activate
echo   python -m pip install -r requirements.txt
echo.
pause
exit /b 1

:import_error
echo.
echo Python packages were installed, but they could not be imported.
echo OpenCV may need the Microsoft Visual C++ Redistributable:
echo   https://aka.ms/vs/17/release/vc_redist.x64.exe
echo If the problem continues, delete .venv and run install.bat again.
echo.
pause
exit /b 1

:optional_check
echo.
echo == Optional language and font support ==
set "OPTIONAL_STATUS=0"
if defined TESSERACT_EXE set "TESSERACT_CMD=%TESSERACT_EXE%"
if not exist "scripts\check_optional.py" (
    echo scripts\check_optional.py is missing; cannot check optional support.
    echo See INSTALL.md for the Noto font and extra language packages.
    set "OPTIONAL_STATUS=1"
    goto :summary
)
".venv\Scripts\python.exe" scripts\check_optional.py --family windows
if errorlevel 1 set "OPTIONAL_STATUS=1"
goto :summary

:summary
echo.
echo == Summary ==
if "%TESSERACT_STATE%"=="ok" (
    if "%OPTIONAL_STATUS%"=="0" (
        echo Everything is installed.
    ) else (
        echo Everything required is installed.
        echo Optional language/font support is incomplete; see the section above.
    )
) else (
    echo InkPatch is not fully installed.
    if "%TESSERACT_STATE%"=="missing" (
        echo Tesseract OCR is not installed.
        echo Install it with:
        echo   winget install -e --id UB-Mannheim.TesseractOCR
        echo Or download the UB Mannheim installer:
        echo   https://github.com/UB-Mannheim/tesseract/wiki
        echo Select English language data during installation.
    ) else (
        echo Tesseract is installed, but the English language data for eng is missing.
        echo Re-run the Tesseract installer and select English.
    )
)

echo.
echo == Run InkPatch ==
echo   cd /d "%~dp0"
echo   .venv\Scripts\activate
echo   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo Then open http://127.0.0.1:8000
if not "%TESSERACT_STATE%"=="ok" (
    echo.
    echo OCR will be unavailable until Tesseract is installed.
    echo.
    pause
    exit /b 1
)
echo.
pause
exit /b 0
