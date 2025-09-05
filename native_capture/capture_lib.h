#pragma once

#ifdef _WIN32
    #ifdef CAPTURE_LIB_EXPORTS
        #define CAPTURE_LIB_API __declspec(dllexport)
    #else
        #define CAPTURE_LIB_API __declspec(dllimport)
    #endif
#else
    #define CAPTURE_LIB_API
#endif

#include <stdint.h>

#ifdef _WIN32
    #include <windows.h>
#endif

#ifdef __cplusplus
extern "C" {
#endif

// Error codes
typedef enum {
    CAPTURE_ERROR_NONE = 0,
    CAPTURE_ERROR_NOT_INITIALIZED = -1,
    CAPTURE_ERROR_INITIALIZATION_FAILED = -2,
    CAPTURE_ERROR_INVALID_PARAMETER = -3,
    CAPTURE_ERROR_CAPTURE_FAILED = -4,
    CAPTURE_ERROR_OUT_OF_MEMORY = -5,
    CAPTURE_ERROR_UNSUPPORTED = -6
} CaptureError;

// Pixel formats
typedef enum {
    CAPTURE_FORMAT_BGRA = 0,
    CAPTURE_FORMAT_RGBA = 1,
    CAPTURE_FORMAT_RGB = 2
} CaptureFormat;

// Capture handle
typedef void* CaptureHandle;

// Capture region structure
typedef struct {
    int x;
    int y;
    int width;
    int height;
} CaptureRegion;

// Capture configuration structure
typedef struct {
    int capture_interval_ms;  // Capture interval in milliseconds
    CaptureRegion region;     // Capture region (0,0,0,0 means full screen)
    int enable_region;        // Enable region capture (0=false, 1=true)
} CaptureConfig;

// Frame structure
typedef struct {
    int width;
    int height;
    int stride;
    int64_t timestamp;
    uint8_t* data;
    size_t data_size;
    CaptureFormat format;
} CaptureFrame;

// Window information structure
typedef struct {
    HWND hwnd;
    char title[256];
} WindowInfo;

// Library initialization and cleanup
CAPTURE_LIB_API CaptureError capture_init();
CAPTURE_LIB_API void capture_cleanup();
CAPTURE_LIB_API const char* capture_get_error_string(CaptureError error);
CAPTURE_LIB_API CaptureError capture_get_last_error();

// Session management
CAPTURE_LIB_API CaptureHandle capture_create_window_session(HWND window);
CAPTURE_LIB_API CaptureHandle capture_create_monitor_session(int monitor_index);
CAPTURE_LIB_API CaptureHandle capture_create_window_session_with_config(HWND window, const CaptureConfig* config);
CAPTURE_LIB_API CaptureHandle capture_create_monitor_session_with_config(int monitor_index, const CaptureConfig* config);
CAPTURE_LIB_API CaptureError capture_start(CaptureHandle handle);
CAPTURE_LIB_API CaptureError capture_stop(CaptureHandle handle);
CAPTURE_LIB_API void capture_destroy_session(CaptureHandle handle);
CAPTURE_LIB_API CaptureError capture_set_config(CaptureHandle handle, const CaptureConfig* config);
CAPTURE_LIB_API CaptureError capture_get_config(CaptureHandle handle, CaptureConfig* config);

// Frame operations
CAPTURE_LIB_API CaptureFrame* capture_get_frame(CaptureHandle handle);
CAPTURE_LIB_API void capture_free_frame(CaptureFrame* frame);

// Frame cache operations
CAPTURE_LIB_API void capture_clear_frame_cache(CaptureHandle handle);

// Utility functions
CAPTURE_LIB_API int capture_enum_windows(WindowInfo* windows, int max_count);
CAPTURE_LIB_API bool capture_get_window_title(HWND window, char* title, int title_size);

#ifdef __cplusplus
}
#endif