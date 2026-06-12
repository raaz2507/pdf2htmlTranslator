@echo off

start "Ollama Server" cmd /k "ollama serve"

start "IndicTrans API" powershell -NoExit -ExecutionPolicy Bypass -File "run_indictrans.ps1"

start "Translator API" powershell -NoExit -ExecutionPolicy Bypass -File "run.ps1"