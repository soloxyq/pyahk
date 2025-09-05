cmake_minimum_required(VERSION 3.20)
project(capture_lib VERSION 1.0.0)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Debug output option
option(ENABLE_DEBUG_OUTPUT "Enable debug output via OutputDebugString" OFF)
if(ENABLE_DEBUG_OUTPUT)
    add_definitions(-DENABLE_DEBUG_OUTPUT)
    message(STATUS "Debug output: ENABLED")
else()
    message(STATUS "Debug output: DISABLED")
endif()

# Set Windows-specific settings
set(CMAKE_SYSTEM_NAME Windows)

# Windows SDK and DirectX paths
set(WINDOWS_SDK_PATH "C:/Program Files (x86)/Windows Kits/10")
set(CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION "10.0.26100.0")

# Include directories
include_directories(
    "${WINDOWS_SDK_PATH}/Include/10.0.26100.0/um"
    "${WINDOWS_SDK_PATH}/Include/10.0.26100.0/shared"
)

# Library directories
link_directories(
    "${WINDOWS_SDK_PATH}/Lib/10.0.26100.0/um/x64"
)

# Source files
set(SOURCES
    capture_lib.cpp
)

# Create shared library
add_library(capture_lib SHARED ${SOURCES})

# Compiler options for capture_lib
target_compile_options(capture_lib PRIVATE
    /W3
)

# Define basic Windows macros
target_compile_definitions(capture_lib PRIVATE
    WIN32_LEAN_AND_MEAN
    _UNICODE
    UNICODE
    CAPTURE_LIB_EXPORTS
)

# Link libraries
target_link_libraries(capture_lib
    d3d11
    dxgi
    user32
    kernel32
)

# Set output properties
set_target_properties(capture_lib PROPERTIES
    OUTPUT_NAME "capture_lib"
    PREFIX ""
    SUFFIX ".dll"
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)

# Copy DLL to output directory
add_custom_command(TARGET capture_lib POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        $<TARGET_FILE:capture_lib>
        ${CMAKE_BINARY_DIR}/bin/
    COMMENT "Copying capture_lib.dll to bin directory"
)

# Hello world executable for testing
add_executable(hello hello.cpp)
target_link_libraries(hello capture_lib)
set_target_properties(hello PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)

# Installation rules
install(TARGETS capture_lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
    ARCHIVE DESTINATION lib
)

install(FILES capture_lib.h DESTINATION include)

# Print configuration info
message(STATUS "Windows SDK Version: ${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}")
message(STATUS "C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "Build Type: ${CMAKE_BUILD_TYPE}")