$ErrorActionPreference = 'Stop'

Write-Host 'Growth Gradual - Windows WeasyPrint setup' -ForegroundColor Cyan
Write-Host ''

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host 'winget is not installed. Install/enable Microsoft App Installer first.' -ForegroundColor Yellow
    exit 1
}

Write-Host '1) Installing/updating MSYS2...' -ForegroundColor Green
winget install --id MSYS2.MSYS2 -e --accept-source-agreements --accept-package-agreements

Write-Host ''
Write-Host '2) Open "MSYS2 UCRT64" from the Start menu and run:' -ForegroundColor Green
Write-Host '   pacman -Syu'
Write-Host '   # If MSYS2 asks you to close the shell, close it, reopen MSYS2 UCRT64, then run:'
Write-Host '   pacman -Su'
Write-Host '   pacman -S --needed mingw-w64-ucrt-x86_64-pango'
Write-Host ''
Write-Host '3) Back in Windows PowerShell/CMD, set the DLL directory:' -ForegroundColor Green
Write-Host '   setx WEASYPRINT_DLL_DIRECTORIES "C:\msys64\ucrt64\bin"'
Write-Host ''
Write-Host '4) Close and reopen your terminal, then from backend run:' -ForegroundColor Green
Write-Host '   py -3.12 -m pip install -r requirements.txt'
Write-Host '   py -3.12 -m weasyprint --info'
Write-Host ''
Write-Host 'The official WeasyPrint Windows instructions use MSYS2 UCRT64 Pango and WEASYPRINT_DLL_DIRECTORIES.' -ForegroundColor Cyan
