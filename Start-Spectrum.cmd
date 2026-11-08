@echo off
cd /d "%~dp0"
if not "%~1"=="" goto run
if exist ".tools\spectrum\session.json" (
    python -m z80tm spectrum --resume ".tools\spectrum\session.json"
    goto done
)
if exist ".tools\spectrum\boot.json" (
    python -m z80tm spectrum --resume ".tools\spectrum\boot.json"
    goto done
)
:run
python -m z80tm spectrum %*
:done
if errorlevel 1 pause
