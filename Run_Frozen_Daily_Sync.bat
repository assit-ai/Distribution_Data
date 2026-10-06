@echo off
title Paragon Agro - Daily ERP Auto Sync (Frozen, Chicken, Egg)
color 0b
echo ======================================================================
echo          PARAGON AGRO LTD. - DAILY SALES PORTAL AUTO SYNC
echo          Categories: Frozen Foods, Process Chicken, Branded Eggs
echo ======================================================================
echo.
echo Please enter the Date for report (Format: DD/MM/YYYY)
echo Or simply press [Enter] to sync for Today (%date%):
set /p TARGET_DATE="Date [DD/MM/YYYY]: "

echo.
echo Select Category to Sync:
echo   1. All Categories (Frozen + Chicken + Egg) [Default]
echo   2. Frozen Foods
echo   3. Process Chicken
echo   4. Branded Eggs
set /p CAT_CHOICE="Enter choice [1-4, Default=1]: "

set CAT_ARG=all
if "%CAT_CHOICE%"=="2" set CAT_ARG=Frozen
if "%CAT_CHOICE%"=="3" set CAT_ARG=Chicken
if "%CAT_CHOICE%"=="4" set CAT_ARG=Egg

echo.
echo Running Automated Poloxy Extraction (Category: %CAT_ARG%)...
python sync_frozen_daily.py %TARGET_DATE% --category=%CAT_ARG%

echo.
echo ======================================================================
echo Synchronization completed! Press any key to exit.
pause >nul
