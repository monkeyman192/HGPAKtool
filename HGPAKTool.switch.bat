@echo off
SETLOCAL EnableDelayedExpansion ENABLEEXTENSIONS
cd /d "%~dp0"

python ./hgpaktool/hgpaktool.py -U --platform switch %*

pause