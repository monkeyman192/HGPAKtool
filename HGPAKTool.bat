@echo off
SETLOCAL EnableDelayedExpansion ENABLEEXTENSIONS
cd /d "%~dp0"

python ./HGPAKTool/hgpaktool.py -U -N %*

pause