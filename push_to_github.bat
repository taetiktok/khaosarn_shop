@echo off
cd /d "%~dp0"
title Push to GitHub
chcp 65001 >nul

echo ========================================
echo   อัพโหลดโค้ดขึ้น GitHub
echo ========================================
echo.
echo   GitHub: taetiktok/khaosarn_shop
echo.
echo กำลัง setup git...

git init
git config user.email "taetiktok4459@gmail.com"
git config user.name "taetiktok"
git add .
git commit -m "deploy: shop v3"
git branch -M main
git remote remove origin 2>nul
git remote add origin https://github.com/taetiktok/khaosarn_shop.git

echo.
echo ======================================================
echo   กำลัง push โค้ดขึ้น GitHub...
echo   ถ้าถามรหัสผ่าน ให้ใส่ Personal Access Token
echo   (ไม่ใช่ password GitHub ปกติ!)
echo.
echo   สร้าง Token ที่:
echo   github.com/settings/tokens/new
echo   เลือก scope: repo แล้ว Generate
echo ======================================================
echo.

git push -u origin main

echo.
echo ========================================
if %errorlevel%==0 (
    echo   สำเร็จ!
    echo   https://github.com/taetiktok/khaosarn_shop
) else (
    echo   มีปัญหา - ดูข้อความด้านบน
)
echo ========================================
pause
