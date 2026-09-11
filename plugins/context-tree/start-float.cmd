@echo off
set "CONTEXT_TREE_HOME=%USERPROFILE%\.context-tree"
wmic process where "CommandLine like '%%context_tree_float.py%%'" get ProcessId 2>nul | findstr /r "[0-9]" >nul
if %ERRORLEVEL%==0 exit /b 0
start "Context Tree Float" /min pythonw "%~dp0scripts\context_tree_float.py" %*
