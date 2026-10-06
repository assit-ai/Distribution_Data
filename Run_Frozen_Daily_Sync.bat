@echo off
title Paragon Agro - Daily ERP Auto Sync (Frozen, Chicken, Egg)
color 0b
echo ======================================================================
echo          PARAGON AGRO LTD. - DAILY SALES PORTAL AUTO SYNC
echo          Categories: Frozen Foods, Process Chicken, Branded Eggs
echo ======================================================================
echo.
echo Please enter Start Date (Format: DD/MM/YYYY)
echo Or simply press [Enter] for Today (%date%):
set /p START_DATE="Start Date [DD/MM/YYYY]: "

echo.
echo Please enter End Date (Format: DD/MM/YYYY)
echo Or press [Enter] for same as Start Date:
set /p END_DATE="End Date [DD/MM/YYYY]: "

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
if "%START_DATE%"=="" (
    python sync_frozen_daily.py --category=%CAT_ARG%
) else (
    if "%END_DATE%"=="" (
        python sync_frozen_daily.py %START_DATE% --category=%CAT_ARG%
    ) else (
        python sync_frozen_daily.py %START_DATE% %END_DATE% --category=%CAT_ARG%
    )
)

echo.
echo ======================================================================
echo Synchronization completed! Press any key to exit.
pause >nul
