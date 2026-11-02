@echo off
chcp 65001 >nul
rem ============================================================================
rem  ScarletMora 打包脚本
rem
rem  双击本文件即可，或在命令行里：  build.bat
rem
rem  做的事情：
rem    app\main.py 做入口，产出一份 ScarletMora.exe（单文件、无控制台窗口）
rem
rem  要点（改的时候别丢）：
rem    --collect-data sudachidict_core   kana.py 用 sudachi 分词，词典是数据文件，
rem                                      不打进去的话"假名/モーラ"会崩
rem    --exclude-module torch/transformers/...  分析是调外部 Python 跑的
rem                                      （worker.py），torch 进 exe 会大到几个 GB
rem    --exclude-module PIL/scipy/setuptools     numpy / huggingface 顺带拖进来的，
rem                                      本程序不用，排除后体积回到 ~100MB
rem
rem  注意：worker.py 不进 exe。分析时用的是 exe 旁边 app\scarlet\worker.py，
rem        所以打包后 app\ 文件夹必须跟 exe 放在一起，不要删。
rem ============================================================================

setlocal
cd /d "%~dp0"

set PY=python
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
)

echo [1/4] 检查 PyInstaller ...
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo     没装 PyInstaller。先跑：  "%PY%" -m pip install pyinstaller
    pause
    exit /b 1
)

echo     备份旧的 exe ...
if exist ScarletMora.exe (
    copy /y ScarletMora.exe ScarletMora.exe.bak >nul
)

echo [2/4] 打包中（2~4 分钟）...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name ScarletMora ^
    --distpath . --workpath build\work --specpath build ^
    --paths app ^
    --collect-data sudachidict_core ^
    --exclude-module torch ^
    --exclude-module torchaudio ^
    --exclude-module transformers ^
    --exclude-module tokenizers ^
    --exclude-module huggingface_hub ^
    --exclude-module safetensors ^
    --exclude-module PIL ^
    --exclude-module scipy ^
    --exclude-module setuptools ^
    --exclude-module pkg_resources ^
    app\main.py

if errorlevel 1 (
    echo.
    echo 打包失败，看上面最后几行报错。
    pause
    exit /b 1
)

echo [3/4] 自检 ...
if exist logs\selftest.txt del /q logs\selftest.txt
"%~dp0ScarletMora.exe" --selftest
echo.
type logs\selftest.txt
findstr /c:"SELFTEST OK" logs\selftest.txt >nul
if errorlevel 1 (
    echo 自检没过，见上面 logs\selftest.txt。
    pause
    exit /b 1
)

if exist build rmdir /s /q build

rem   用户版 zip = exe + app\ + models\ + 说明。models\ 忘了加，用户那边就是
rem   「缺少模型」。这一步别跳过（只想出轻量包就手动跑 make_release.py --no-model）。
echo [4/4] 打用户版 zip（含 models\，约 470MB，要一会儿）...
"%PY%" make_release.py
if errorlevel 1 (
    echo.
    echo 打 zip 失败（见上面）。模型没下载的话，按 README 的「声学模型」补。
    pause
    exit /b 1
)

echo.
echo 完成：SELFTEST OK + 用户版 zip（ScarletMora-<版本>-win64.zip）。
echo 用户解压后 app\ 和 models\ 都别删。
pause
