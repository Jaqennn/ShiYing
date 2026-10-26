@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安装依赖...
python -m pip install -r requirements.txt pyinstaller || goto :err
echo [2/2] 打包...
python -m PyInstaller --noconfirm --clean --windowed --name ShiYing --icon assets\icon.ico --add-data "web;web" main.py || goto :err
echo.
echo 完成：dist\ShiYing\ShiYing.exe
echo ShiYing.exe 要和旁边的 _internal 文件夹放在一起；想换位置就移动整个 ShiYing 文件夹
start "" explorer "dist\ShiYing"
pause
exit /b 0
:err
echo.
echo 构建失败，请把上面的报错截图发我
pause
exit /b 1
