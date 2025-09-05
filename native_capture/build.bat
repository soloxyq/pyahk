@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Game Screen Capture Library Build Script
echo ========================================

:: Setup Visual Studio Environment
echo Setting up Visual Studio environment...
set "VCVARSALL_PATH=D:\devtools\vs2022\msbuild\VC\Auxiliary\Build\vcvarsall.bat"
if not exist "%VCVARSALL_PATH%" (
    echo Error: vcvarsall.bat not found
    echo Path: %VCVARSALL_PATH%
    pause
    exit /b 1
)

:: Call vcvarsall.bat to setup environment
call "%VCVARSALL_PATH%" x64
if %errorlevel% neq 0 (
    echo Error: Visual Studio environment setup failed
    pause
    exit /b 1
)
echo Visual Studio environment setup successful

:: Check if compiler is available
where cl >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Visual Studio compiler not found
    pause
    exit /b 1
)
echo Visual Studio compiler found

:: Check CMake installation
echo Checking CMake installation...
where cmake >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: CMake not found
    echo Please download and install CMake from https://cmake.org/download/
    pause
    exit /b 1
)
echo CMake found

:: Check Windows SDK
echo Checking Windows SDK...
set "SDK_PATH=C:\Program Files (x86)\Windows Kits\10\Include"
if not exist "%SDK_PATH%" (
    echo Error: Windows 10 SDK not found
    echo Please install Windows 10 SDK
    pause
    exit /b 1
)
echo Windows 10 SDK found

:: Create build directory
echo Creating build directory...
if exist build (
    echo Cleaning old build directory...
    rmdir /s /q build
)
mkdir build
cd build

:: Configure CMake
echo Configuring CMake project...
if "%1"=="debug" (
    echo Building with DEBUG OUTPUT enabled...
    cmake .. -G "Visual Studio 17 2022" -A x64 -DENABLE_DEBUG_OUTPUT=ON
) else (
    echo Building with DEBUG OUTPUT disabled...
    cmake .. -G "Visual Studio 17 2022" -A x64 -DENABLE_DEBUG_OUTPUT=OFF
)

if %errorlevel% neq 0 (
    echo Error: CMake configuration failed
    cd ..
    pause
    exit /b 1
)
echo CMake configuration successful

:: Build project
echo Building project...
cmake --build . --config Release
if %errorlevel% neq 0 (
    echo Error: Build failed
    cd ..
    pause
    exit /b 1
)
echo Build successful

:: Check output files
echo Checking output files...
if exist "bin\Release\capture_lib.dll" (
    echo DLL file generated successfully: bin\Release\capture_lib.dll
    
    :: Copy DLL to parent directory
    copy "bin\Release\capture_lib.dll" "..\capture_lib.dll"
    if %errorlevel% equ 0 (
        echo DLL copied to main directory
    )
) else (
    echo Error: Generated DLL file not found
    cd ..
    pause
    exit /b 1
)

cd ..

echo ========================================
echo Build completed!
echo DLL file location: capture_lib.dll
echo You can now run Python test scripts
echo ========================================

pause