@echo off
title Paragon Agro - Frozen Foods Daily ERP Auto Sync
color 0b
echo ======================================================================
echo          PARAGON AGRO LTD. - DAILY SALES PORTAL AUTO SYNC
echo                    Category: Frozen Foods (Poloxy ERP)
echo ======================================================================
echo.
echo Please enter the Date for report (Format: DD/MM/YYYY)
echo Or simply press [Enter] to sync for Today (%date%):
set /p TARGET_DATE="Date [DD/MM/YYYY]: "

echo.
echo Running Automated Poloxy Extraction...
python sync_frozen_daily.py %TARGET_DATE%

echo.
echo ======================================================================
echo Synchronization completed! Press any key to exit.
pause >nul
