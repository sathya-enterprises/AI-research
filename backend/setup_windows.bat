@echo off
setlocal
cd /d %~dp0

echo === Growth Gradual backend dependency setup ===
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt

echo.
echo If WeasyPrint reports libgobject-2.0-0 missing, install MSYS2 UCRT64 Pango:
echo   winget install --id MSYS2.MSYS2 -e
 echo Then open MSYS2 UCRT64 and run:
echo   pacman -Syu
echo   pacman -Su
echo   pacman -S --needed mingw-w64-ucrt-x86_64-pango
 echo Then in a NEW Windows terminal:
echo   setx WEASYPRINT_DLL_DIRECTORIES "C:\msys64\ucrt64\bin"
echo   py -3.12 -m weasyprint --info
endlocal
