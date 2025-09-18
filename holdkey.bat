@echo off
REM ---------------- 配置 ----------------
REM 如果系统 PATH 没有 AutoHotkey.exe，请把下面改为完整路径，例如:
REM set "AHK_EXE=D:\Program Files\AutoHotkey\v2\AutoHotkey.exe"
set "AHK_EXE=D:\Program Files\AutoHotkey\v2\AutoHotkey.exe"

REM 与服务脚本同目录下的文件名
set "SCRIPT=%~dp0hold_server.ahk"

REM 服务端在脚本中使用的窗口标题（与脚本内一致）
set "WIN_TITLE=HoldServer_Window_UniqueName_12345"
REM -----------------------------------------

REM 使用 PowerShell 调用 FindWindow 检查窗口是否存在 (返回码 0 = 存在)
powershell -NoProfile -Command "Add-Type -Namespace Win32 -Name User32 -MemberDefinition '[DllImport(\"user32.dll\",CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);' ; if ([Win32.User32]::FindWindow($null, '%WIN_TITLE%') -ne [IntPtr]::Zero) { exit 0 } else { exit 2 }"

if %ERRORLEVEL% EQU 0 (
    echo AHK server 已在运行（窗口 "%WIN_TITLE%" 存在）。
    exit /b 0
)

echo 启动 AHK server...
start "" "%AHK_EXE%" "%SCRIPT%"

if %ERRORLEVEL% EQU 0 (
    echo 已启动（若窗口依然未响应，请确认 AutoHotkey 已安装且脚本路径正确）。
) else (
    echo 启动失败：请检查 AHK_EXE 设置或脚本路径。
)
exit /b 0
