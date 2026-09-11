@echo off
setlocal
codex plugin add context-tree@context-tree-local
if errorlevel 1 (
  echo Close Codex completely, including the tray process, then run this file again.
  exit /b 1
)
echo Context Tree plugin updated. Start a new Codex task.
