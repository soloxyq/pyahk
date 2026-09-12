#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <objbase.h>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <chrono>
#include <atomic>

#include "capture_lib.h"

// Get current time in milliseconds
static int64_t GetCurrentTimeMs() {
    auto now = std::chrono::steady_clock::now();
    auto duration = now.time_since_epoch();
    return std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();
}

#include <dxgi1_2.h>
#include <d3d11.h>
#include <vector>
#include <memory>
#include <mutex>
#include <map>
#include <algorithm>
#include <limits>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3d11.lib")

// Debug output helper - can be controlled by compile options
#ifdef ENABLE_DEBUG_OUTPUT
#define DEBUG_PRINT(msg) do { \
    std::ostringstream oss; \
    oss << "[Capture Lib Debug] " << msg << std::endl; \
    OutputDebugStringA(oss.str().c_str()); \
} while(0)
#else
#define DEBUG_PRINT(msg) do { } while(0)
#endif

// Global variables
static bool g_initialized = false;
static unsigned int g_init_refcount = 0;
static thread_local CaptureError g_last_error = CAPTURE_ERROR_NONE;
static IDXGIFactory1* g_dxgi_factory = nullptr;
// Desktop Duplication and D3D11 immediate contexts are not thread-safe.
// Serializing exported operations also keeps handles alive during lookup/use.
static std::recursive_mutex g_api_mutex;
static std::map<CaptureHandle, class DXGICaptureSession*> g_sessions;

struct OutputSelection {
    int adapter_index = -1;
    int output_index = -1;
};

static bool IsConfigStructurallyValid(const CaptureConfig* config) {
    return config && config->capture_interval_ms >= 0 &&
        (config->enable_region == 0 || config->enable_region == 1) &&
        (!config->enable_region ||
         (config->region.width > 0 && config->region.height > 0));
}

// DXGI Capture Session
class DXGICaptureSession {
public:
    HWND target_window = nullptr;
    OutputSelection output_selection;
    bool is_running = false;
    
    // Capture configuration
    CaptureConfig config;
    
    // DXGI objects
    IDXGIAdapter1* adapter = nullptr;
    IDXGIOutput* output = nullptr;
    IDXGIOutput1* output1 = nullptr;
    IDXGIOutputDuplication* duplication = nullptr;
    ID3D11Device* d3d_device = nullptr;
    ID3D11DeviceContext* d3d_context = nullptr;
    
    // --- Zero-Copy Frame Buffers ---
    std::vector<uint8_t> buffer_a;
    std::vector<uint8_t> buffer_b;

    // This struct is what the Python side will get a pointer to.
    // Its data pointer will be atomically updated.
    CaptureFrame shared_frame;

    // Atomic pointer to the currently readable buffer's data.
    std::atomic<uint8_t*> current_read_buffer{nullptr};

    // Which buffer is the capture thread currently writing to?
    // (The other one is the read buffer)
    bool writing_to_a = true;
    
    int frame_width = 0;
    int frame_height = 0;
    int original_width = 0;   // 原始屏幕宽度
    int original_height = 0;  // 原始屏幕高度
    int output_left = 0;
    int output_top = 0;
    int frame_left = 0;
    int frame_top = 0;
    
    // Staging texture for GPU-to-CPU transfer (reused across captures)
    ID3D11Texture2D* staging_texture = nullptr;
    int staging_width = 0;
    int staging_height = 0;
    
    // Timing
    int64_t last_capture_time = 0;
    
    ~DXGICaptureSession() {
        cleanup();
    }
    
    void cleanup() {
        if (staging_texture) {
            staging_texture->Release();
            staging_texture = nullptr;
        }
        if (duplication) {
            duplication->Release();
            duplication = nullptr;
        }
        if (output1) {
            output1->Release();
            output1 = nullptr;
        }
        if (output) {
            output->Release();
            output = nullptr;
        }
        if (d3d_context) {
            d3d_context->Release();
            d3d_context = nullptr;
        }
        if (d3d_device) {
            d3d_device->Release();
            d3d_device = nullptr;
        }
        if (adapter) {
            adapter->Release();
            adapter = nullptr;
        }
        staging_width = 0;
        staging_height = 0;
        original_width = 0;
        original_height = 0;
        frame_width = 0;
        frame_height = 0;
        shared_frame.data = nullptr;
        shared_frame.data_size = 0;
        current_read_buffer.store(nullptr);
    }
};

static bool IsRegionWithinCurrentOutput(
    const DXGICaptureSession* session, const CaptureConfig& config
) {
    if (!config.enable_region) return true;
    if (session->original_width <= 0 || session->original_height <= 0) {
        return false;
    }
    const CaptureRegion& region = config.region;
    const int64_t region_right = static_cast<int64_t>(region.x) + region.width;
    const int64_t region_bottom = static_cast<int64_t>(region.y) + region.height;
    const int64_t output_right = static_cast<int64_t>(session->output_left) +
        session->original_width;
    const int64_t output_bottom = static_cast<int64_t>(session->output_top) +
        session->original_height;
    return region.width > 0 && region.height > 0 &&
        region.x >= session->output_left && region.y >= session->output_top &&
        region_right <= output_right && region_bottom <= output_bottom;
}

// Helper functions
static bool InitializeDXGI() {
    if (g_dxgi_factory) {
        return true;
    }
    
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&g_dxgi_factory);
    if (FAILED(hr)) {
        return false;
    }
    
    return true;
}

static bool GetOutputSelectionForMonitor(HMONITOR monitor, OutputSelection* selection) {
    if (!monitor || !selection || !g_dxgi_factory) return false;
    IDXGIAdapter1* adapter = nullptr;
    for (UINT i = 0; g_dxgi_factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
        IDXGIOutput* output = nullptr;
        for (UINT j = 0; adapter->EnumOutputs(j, &output) != DXGI_ERROR_NOT_FOUND; ++j) {
            DXGI_OUTPUT_DESC desc;
            const bool matches = SUCCEEDED(output->GetDesc(&desc)) && desc.Monitor == monitor;
            output->Release();
            if (matches) {
                adapter->Release();
                selection->adapter_index = static_cast<int>(i);
                selection->output_index = static_cast<int>(j);
                return true;
            }
        }
        adapter->Release();
    }
    return false;
}

static bool GetOutputSelectionForWindow(HWND hwnd, OutputSelection* selection) {
    return GetOutputSelectionForMonitor(
        MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), selection
    );
}

// monitor_index is zero-based across all DXGI outputs, not an adapter index.
static bool GetOutputSelectionByGlobalIndex(int monitor_index, OutputSelection* selection) {
    if (monitor_index < 0 || !selection || !g_dxgi_factory) return false;
    int current_index = 0;
    IDXGIAdapter1* adapter = nullptr;
    for (UINT i = 0; g_dxgi_factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
        IDXGIOutput* output = nullptr;
        for (UINT j = 0; adapter->EnumOutputs(j, &output) != DXGI_ERROR_NOT_FOUND; ++j) {
            output->Release();
            if (current_index++ == monitor_index) {
                adapter->Release();
                selection->adapter_index = static_cast<int>(i);
                selection->output_index = static_cast<int>(j);
                return true;
            }
        }
        adapter->Release();
    }
    return false;
}

static bool SetupDuplication(DXGICaptureSession* session) {
    HRESULT hr;
    const auto fail_setup = [session]() {
        session->cleanup();
        return false;
    };
    
    // Get adapter
    hr = g_dxgi_factory->EnumAdapters1(session->output_selection.adapter_index, &session->adapter);
    if (FAILED(hr)) {
        return fail_setup();
    }

    // Desktop Duplication requires the D3D device to be created on the same
    // adapter as the selected output. A single default-adapter device fails on
    // cross-GPU multi-monitor systems even when output enumeration is correct.
    D3D_FEATURE_LEVEL feature_levels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
    };
    hr = D3D11CreateDevice(
        session->adapter,
        D3D_DRIVER_TYPE_UNKNOWN,
        nullptr,
        0,
        feature_levels,
        ARRAYSIZE(feature_levels),
        D3D11_SDK_VERSION,
        &session->d3d_device,
        nullptr,
        &session->d3d_context
    );
    if (hr == E_INVALIDARG) {
        // Older Windows runtimes reject a feature-level list containing 11_1.
        if (session->d3d_context) {
            session->d3d_context->Release();
            session->d3d_context = nullptr;
        }
        if (session->d3d_device) {
            session->d3d_device->Release();
            session->d3d_device = nullptr;
        }
        hr = D3D11CreateDevice(
            session->adapter,
            D3D_DRIVER_TYPE_UNKNOWN,
            nullptr,
            0,
            &feature_levels[1],
            1,
            D3D11_SDK_VERSION,
            &session->d3d_device,
            nullptr,
            &session->d3d_context
        );
    }
    if (FAILED(hr)) {
        return fail_setup();
    }
    
    // Get output
    hr = session->adapter->EnumOutputs(session->output_selection.output_index, &session->output);
    if (FAILED(hr)) {
        return fail_setup();
    }
    
    // Get output1 interface
    hr = session->output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&session->output1);
    if (FAILED(hr)) {
        return fail_setup();
    }
    
    // Create desktop duplication
    hr = session->output1->DuplicateOutput(session->d3d_device, &session->duplication);
    if (FAILED(hr)) {
        return fail_setup();
    }
    
    // Get output description
    DXGI_OUTPUT_DESC desc;
    hr = session->output->GetDesc(&desc);
    if (FAILED(hr)) {
        return fail_setup();
    }
    
    session->original_width = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left;
    session->original_height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top;
    session->output_left = desc.DesktopCoordinates.left;
    session->output_top = desc.DesktopCoordinates.top;

    if (!IsRegionWithinCurrentOutput(session, session->config)) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return fail_setup();
    }
    
    // Calculate buffer size based on actual capture region
    int buffer_width = session->config.enable_region ? 
        session->config.region.width : session->original_width;
    int buffer_height = session->config.enable_region ? 
        session->config.region.height : session->original_height;
    if (buffer_width <= 0 || buffer_height <= 0) return fail_setup();
    
    // Calculate required size (BGRA = 4 bytes per pixel)
    const size_t width_size = static_cast<size_t>(buffer_width);
    const size_t height_size = static_cast<size_t>(buffer_height);
    if (height_size > std::numeric_limits<size_t>::max() / width_size / 4) {
        g_last_error = CAPTURE_ERROR_OUT_OF_MEMORY;
        return fail_setup();
    }
    size_t required_size = width_size * height_size * 4;
    
    // Allocate 110% of required size to handle minor resolution changes
    const size_t reserve_extra = required_size / 10;
    if (required_size > std::numeric_limits<size_t>::max() - reserve_extra) {
        g_last_error = CAPTURE_ERROR_OUT_OF_MEMORY;
        return fail_setup();
    }
    size_t allocate_size = required_size + reserve_extra;
    
    try {
        session->buffer_a.resize(allocate_size);
        session->buffer_b.resize(allocate_size);
        DEBUG_PRINT("SetupDuplication: Allocated " << allocate_size 
                   << " bytes for " << buffer_width << "x" << buffer_height);
    } catch (const std::bad_alloc&) {
        g_last_error = CAPTURE_ERROR_OUT_OF_MEMORY;
        return fail_setup();
    }

    // Set initial state
    session->writing_to_a = true;
    session->current_read_buffer.store(session->buffer_b.data()); // Initially, B is readable (but empty)
    
    // Initialize shared frame structure
    memset(&session->shared_frame, 0, sizeof(CaptureFrame));
    session->shared_frame.format = CAPTURE_FORMAT_BGRA;
    session->frame_width = 0;
    session->frame_height = 0;
    session->frame_left = session->output_left;
    session->frame_top = session->output_top;

    return true;
}

static bool RefreshWindowOutput(DXGICaptureSession* session) {
    if (!session->target_window) {
        if (session->duplication) return true;
        session->cleanup();
        return SetupDuplication(session);
    }
    OutputSelection selection;
    if (!IsWindow(session->target_window) ||
        !GetOutputSelectionForWindow(session->target_window, &selection)) return false;
    if (selection.adapter_index == session->output_selection.adapter_index &&
        selection.output_index == session->output_selection.output_index) {
        if (session->duplication) return true;
        // A previous DuplicateOutput/recovery attempt may have failed after
        // releasing the old objects. Retry on the next poll instead of leaving
        // this live session permanently unable to capture.
        session->cleanup();
        return SetupDuplication(session);
    }

    // Desktop Duplication is output-bound; do not expose frames from the old
    // output after a target window crosses monitors.
    session->cleanup();
    session->output_selection = selection;
    return SetupDuplication(session);
}

static bool CaptureFrameData(DXGICaptureSession* session) {
    if (!session->duplication) {
        DEBUG_PRINT("CaptureFrameData: duplication object is null");
        return false;
    }
    
    // Optimized: Smart frame rate control - dynamically adjust based on system load
    int64_t current_time = GetTickCount64();
    if (session->config.capture_interval_ms > 0) {
        int64_t time_since_last = current_time - session->last_capture_time;
        if (session->frame_width > 0 &&
            time_since_last < session->config.capture_interval_ms) {
            return false; // Too early for next capture
        }
    }
    
    DXGI_OUTDUPL_FRAME_INFO frameInfo;
    IDXGIResource* desktopResource = nullptr;
    
    HRESULT hr = session->duplication->AcquireNextFrame(0, &frameInfo, &desktopResource);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        return false; // No new frame
    }
    if (FAILED(hr)) {
        DEBUG_PRINT("CaptureFrameData: AcquireNextFrame failed with HRESULT: 0x" << std::hex << hr);
        // Attempt to re-initialize duplication
        session->cleanup();
        SetupDuplication(session);
        return false;
    }
    
    session->last_capture_time = current_time;
    
    ID3D11Texture2D* desktopTexture = nullptr;
    hr = desktopResource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&desktopTexture);
    desktopResource->Release();
    
    if (FAILED(hr)) {
        session->duplication->ReleaseFrame();
        return false;
    }
    
    D3D11_TEXTURE2D_DESC desc;
    desktopTexture->GetDesc(&desc);
    
    // Reuse staging texture if dimensions match, otherwise recreate
    if (!session->staging_texture || 
        session->staging_width != static_cast<int>(desc.Width) || 
        session->staging_height != static_cast<int>(desc.Height)) {
        
        // Release old staging texture if exists
        if (session->staging_texture) {
            session->staging_texture->Release();
            session->staging_texture = nullptr;
        }
        
        // Create new staging texture
        D3D11_TEXTURE2D_DESC stagingDesc = desc;
        stagingDesc.Usage = D3D11_USAGE_STAGING;
        stagingDesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        stagingDesc.BindFlags = 0;
        stagingDesc.MiscFlags = 0;
        
        hr = session->d3d_device->CreateTexture2D(&stagingDesc, nullptr, &session->staging_texture);
        if (FAILED(hr)) {
            desktopTexture->Release();
            session->duplication->ReleaseFrame();
            return false;
        }
        
        session->staging_width = desc.Width;
        session->staging_height = desc.Height;
        DEBUG_PRINT("CaptureFrameData: Created staging texture " 
                   << desc.Width << "x" << desc.Height);
    }
    
    session->d3d_context->CopyResource(session->staging_texture, desktopTexture);
    
    D3D11_MAPPED_SUBRESOURCE mapped;
    hr = session->d3d_context->Map(session->staging_texture, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) {
        desktopTexture->Release();
        session->duplication->ReleaseFrame();
        return false;
    }
    
    // Determine capture region and target buffer
    int capture_x = 0, capture_y = 0;
    int capture_width = session->original_width;
    int capture_height = session->original_height;
    session->frame_left = session->output_left;
    session->frame_top = session->output_top;
    
    if (session->config.enable_region) {
        const int64_t region_right = static_cast<int64_t>(session->config.region.x) + session->config.region.width;
        const int64_t region_bottom = static_cast<int64_t>(session->config.region.y) + session->config.region.height;
        const int64_t output_right = static_cast<int64_t>(session->output_left) + session->original_width;
        const int64_t output_bottom = static_cast<int64_t>(session->output_top) + session->original_height;
        if (session->config.region.width <= 0 || session->config.region.height <= 0 ||
            session->config.region.x < session->output_left ||
            session->config.region.y < session->output_top ||
            region_right > output_right || region_bottom > output_bottom) {
            g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
            session->d3d_context->Unmap(session->staging_texture, 0);
            desktopTexture->Release();
            session->duplication->ReleaseFrame();
            return false;
        }
        capture_x = session->config.region.x - session->output_left;
        capture_y = session->config.region.y - session->output_top;
        capture_width = session->config.region.width;
        capture_height = session->config.region.height;
        session->frame_left = session->config.region.x;
        session->frame_top = session->config.region.y;
    }
    
    // Get the write buffer
    std::vector<uint8_t>& write_buffer = session->writing_to_a ? session->buffer_a : session->buffer_b;
    
    // Optimized: Smarter buffer size adjustment
    // Resize buffer if region changes size
    const size_t required_size = static_cast<size_t>(capture_width) *
        static_cast<size_t>(capture_height) * 4;
    if (write_buffer.size() < required_size) {
        try {
            // Pre-allocate slightly larger space to avoid frequent reallocation
            size_t new_size = required_size + (required_size / 10);  // Allocate 10% more
            write_buffer.resize(new_size);
            DEBUG_PRINT("CaptureFrameData: Buffer resized to " << new_size << " bytes (required: " << required_size << ")");
        } catch (const std::bad_alloc&) {
            // handle allocation failure
            session->d3d_context->Unmap(session->staging_texture, 0);
            desktopTexture->Release();
            session->duplication->ReleaseFrame();
            return false;
        }
    }

    uint8_t* src = static_cast<uint8_t*>(mapped.pData);
    uint8_t* dst = write_buffer.data();

    // Optimized: Use more efficient memory copy strategy
    // Copy region row by row with optimized memory access
    const size_t row_bytes = capture_width * 4;
    for (int y = 0; y < capture_height; ++y) {
        uint8_t* row_start_src = src + (capture_y + y) * mapped.RowPitch + capture_x * 4;
        // Use larger block copy to improve cache efficiency
        memcpy(dst + y * row_bytes, row_start_src, row_bytes);
    }
    
    // --- Atomic Swap ---
    // Update the shared frame info for the new frame
    session->frame_width = capture_width;
    session->frame_height = capture_height;
    
    // Atomically publish the new frame
    session->current_read_buffer.store(write_buffer.data());
    
    // Swap buffers for next capture
    session->writing_to_a = !session->writing_to_a;
    
    // Cleanup (staging texture is reused, not released)
    session->d3d_context->Unmap(session->staging_texture, 0);
    desktopTexture->Release();
    session->duplication->ReleaseFrame();
    
    return true;
}

// API implementations
CAPTURE_LIB_API CaptureError capture_init() {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (g_initialized) {
        ++g_init_refcount;
        return CAPTURE_ERROR_NONE;
    }
    
    if (!InitializeDXGI()) {
        g_last_error = CAPTURE_ERROR_INITIALIZATION_FAILED;
        return g_last_error;
    }
    
    g_initialized = true;
    g_init_refcount = 1;
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API void capture_cleanup() {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) {
        return;
    }
    if (g_init_refcount > 1) {
        --g_init_refcount;
        return;
    }
    
    // Cleanup all sessions
    for (auto& pair : g_sessions) {
        delete pair.second;
    }
    g_sessions.clear();
    
    if (g_dxgi_factory) {
        g_dxgi_factory->Release();
        g_dxgi_factory = nullptr;
    }
    
    g_initialized = false;
    g_init_refcount = 0;
}

CAPTURE_LIB_API const char* capture_get_error_string(CaptureError error) {
    switch (error) {
        case CAPTURE_ERROR_NONE:
            return "No error";
        case CAPTURE_ERROR_NOT_INITIALIZED:
            return "Library not initialized";
        case CAPTURE_ERROR_INVALID_PARAMETER:
            return "Invalid parameter";
        case CAPTURE_ERROR_INITIALIZATION_FAILED:
            return "Initialization failed";
        case CAPTURE_ERROR_CAPTURE_FAILED:
            return "Capture failed";
        case CAPTURE_ERROR_UNSUPPORTED:
            return "Operation not supported";
        default:
            return "Unknown error";
    }
}

CAPTURE_LIB_API CaptureError capture_get_last_error() {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    return g_last_error;
}

CAPTURE_LIB_API CaptureHandle capture_create_window_session(HWND window) {
    CaptureConfig default_config = {0};
    default_config.capture_interval_ms = 60;
    default_config.enable_region = false;
    return capture_create_window_session_with_config(window, &default_config);
}

CAPTURE_LIB_API CaptureHandle capture_create_window_session_with_config(HWND window, const CaptureConfig* config) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) {
        g_last_error = CAPTURE_ERROR_NOT_INITIALIZED;
        return nullptr;
    }
    
    if (!window || !IsWindow(window) || !IsConfigStructurallyValid(config)) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return nullptr;
    }
    
    try {
        auto session = new DXGICaptureSession();
        session->target_window = window;
        if (!GetOutputSelectionForWindow(window, &session->output_selection)) {
            delete session;
            g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
            return nullptr;
        }
        session->config = *config;
        
        CaptureHandle handle = reinterpret_cast<CaptureHandle>(session);
        g_sessions[handle] = session;
        
        g_last_error = CAPTURE_ERROR_NONE;
        return handle;
    }
    catch (...) {
        g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        return nullptr;
    }
}

CAPTURE_LIB_API CaptureHandle capture_create_monitor_session(int monitor_index) {
    CaptureConfig default_config = {0};
    default_config.capture_interval_ms = 60;
    default_config.enable_region = false;
    return capture_create_monitor_session_with_config(monitor_index, &default_config);
}

CAPTURE_LIB_API CaptureHandle capture_create_monitor_session_with_config(int monitor_index, const CaptureConfig* config) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) {
        g_last_error = CAPTURE_ERROR_NOT_INITIALIZED;
        return nullptr;
    }
    
    if (monitor_index < 0 || !IsConfigStructurallyValid(config)) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return nullptr;
    }
    
    try {
        auto session = new DXGICaptureSession();
        if (!GetOutputSelectionByGlobalIndex(monitor_index, &session->output_selection)) {
            delete session;
            g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
            return nullptr;
        }
        session->config = *config;
        
        CaptureHandle handle = reinterpret_cast<CaptureHandle>(session);
        g_sessions[handle] = session;
        
        g_last_error = CAPTURE_ERROR_NONE;
        return handle;
    }
    catch (...) {
        g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        return nullptr;
    }
}

CAPTURE_LIB_API CaptureError capture_start(CaptureHandle handle) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) return CAPTURE_ERROR_NOT_INITIALIZED;
    if (!handle) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto session = it->second;
    session->is_running = false;
    session->cleanup();
    if (session->target_window) {
        OutputSelection selection;
        if (!GetOutputSelectionForWindow(session->target_window, &selection)) {
            g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
            return g_last_error;
        }
        session->output_selection = selection;
    }
    g_last_error = CAPTURE_ERROR_NONE;
    if (!SetupDuplication(session)) {
        if (g_last_error == CAPTURE_ERROR_NONE) {
            g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        }
        return g_last_error;
    }
    
    session->is_running = true;
    
    // Perform an initial frame capture to populate a buffer
    if (CaptureFrameData(session)) {
        DEBUG_PRINT("capture_start: Initial frame capture successful");
    } else {
        DEBUG_PRINT("capture_start: Initial frame capture failed, but continuing");
    }
    
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API CaptureError capture_stop(CaptureHandle handle) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) return CAPTURE_ERROR_NOT_INITIALIZED;
    if (!handle) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto session = it->second;
    session->is_running = false;
    session->cleanup();
    
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API void capture_destroy_session(CaptureHandle handle) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!handle) return;
    
    auto it = g_sessions.find(handle);
    if (it != g_sessions.end()) {
        delete it->second;
        g_sessions.erase(it);
    }
}

CAPTURE_LIB_API CaptureFrame* capture_get_frame(CaptureHandle handle) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) {
        g_last_error = CAPTURE_ERROR_NOT_INITIALIZED;
        return nullptr;
    }
    
    if (!handle) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return nullptr;
    }
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return nullptr;
    }
    
    auto session = it->second;
    
    if (!session->is_running) {
        g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        return nullptr;
    }
    if (!RefreshWindowOutput(session)) {
        if (g_last_error == CAPTURE_ERROR_NONE) {
            g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        }
        return nullptr;
    }
    
    // Attempt to capture a new frame. This will atomically update the
    // read buffer pointer if successful.
    CaptureFrameData(session);
    
    // Update the shared frame struct with the latest data from the session.
    // This is safe because the pointer comes from the atomic<>, and width/height
    // are updated after the new data is ready.
    session->shared_frame.data = session->current_read_buffer.load();
    session->shared_frame.width = session->frame_width;
    session->shared_frame.height = session->frame_height;
    session->shared_frame.stride = session->frame_width * 4;
    session->shared_frame.data_size = static_cast<size_t>(session->frame_width) *
        static_cast<size_t>(session->frame_height) * 4;
    session->shared_frame.timestamp = GetCurrentTimeMs();

    if (session->shared_frame.data == nullptr || session->shared_frame.data_size == 0) {
        g_last_error = CAPTURE_ERROR_CAPTURE_FAILED;
        return nullptr;
    }

    g_last_error = CAPTURE_ERROR_NONE;
    return &session->shared_frame;
}

CAPTURE_LIB_API void capture_free_frame(CaptureFrame* frame) {
    // Do nothing. Memory is managed by the session.
}

CAPTURE_LIB_API int capture_enum_windows(WindowInfo* windows, int max_count) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!windows || max_count <= 0) {
        return 0;
    }
    
    struct EnumData {
        WindowInfo* windows;
        int* count;
        int max_count;
    };
    
    int count = 0;
    EnumData data = { windows, &count, max_count };
    
    ::EnumWindows([](HWND hwnd, LPARAM lParam) -> BOOL {
        EnumData* enumData = reinterpret_cast<EnumData*>(lParam);
        
        if (*(enumData->count) >= enumData->max_count) {
            return FALSE;
        }
        
        if (::IsWindowVisible(hwnd)) {
            enumData->windows[*(enumData->count)].hwnd = hwnd;
            ::GetWindowTextA(hwnd, enumData->windows[*(enumData->count)].title, sizeof(enumData->windows[*(enumData->count)].title));
            (*(enumData->count))++;
        }
        
        return TRUE;
    }, reinterpret_cast<LPARAM>(&data));
    
    return count;
}

CAPTURE_LIB_API bool capture_get_window_title(HWND window, char* title, int title_size) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!window || !title || title_size <= 0) {
        return false;
    }
    
    return ::GetWindowTextA(window, title, title_size) > 0;
}

CAPTURE_LIB_API CaptureError capture_set_config(CaptureHandle handle, const CaptureConfig* config) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) return CAPTURE_ERROR_NOT_INITIALIZED;
    if (!handle || !config) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto session = it->second;
    
    if (!IsConfigStructurallyValid(config) ||
        (session->original_width > 0 &&
         !IsRegionWithinCurrentOutput(session, *config))) {
        g_last_error = CAPTURE_ERROR_INVALID_PARAMETER;
        return g_last_error;
    }

    const bool spatial_change =
        session->config.enable_region != config->enable_region ||
        session->config.region.x != config->region.x ||
        session->config.region.y != config->region.y ||
        session->config.region.width != config->region.width ||
        session->config.region.height != config->region.height;
    session->config = *config;
    if (spatial_change) {
        // Never expose a frame captured under the previous rectangle as if it
        // belonged to the new coordinate contract. The next get_frame call
        // must publish a fresh frame first.
        session->frame_width = 0;
        session->frame_height = 0;
        session->shared_frame.data = nullptr;
        session->shared_frame.data_size = 0;
        session->current_read_buffer.store(nullptr);
        session->last_capture_time = 0;
    }
    
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API CaptureError capture_get_config(CaptureHandle handle, CaptureConfig* config) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) return CAPTURE_ERROR_NOT_INITIALIZED;
    if (!handle || !config) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return CAPTURE_ERROR_INVALID_PARAMETER;
    
    auto session = it->second;
    *config = session->config;
    
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API CaptureError capture_get_frame_rect(CaptureHandle handle, CaptureRegion* rect) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    if (!g_initialized) return CAPTURE_ERROR_NOT_INITIALIZED;
    if (!handle || !rect) return CAPTURE_ERROR_INVALID_PARAMETER;
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return CAPTURE_ERROR_INVALID_PARAMETER;
    auto session = it->second;
    if (!session->is_running || session->frame_width <= 0 || session->frame_height <= 0) {
        return CAPTURE_ERROR_CAPTURE_FAILED;
    }
    rect->x = session->frame_left;
    rect->y = session->frame_top;
    rect->width = session->frame_width;
    rect->height = session->frame_height;
    g_last_error = CAPTURE_ERROR_NONE;
    return CAPTURE_ERROR_NONE;
}

CAPTURE_LIB_API void capture_clear_frame_cache(CaptureHandle handle) {
    std::lock_guard<std::recursive_mutex> lock(g_api_mutex);
    // This function is less relevant with the double buffer model,
    // but we can clear the buffers if needed.
    if (!g_initialized || !handle) return;
    
    auto it = g_sessions.find(handle);
    if (it == g_sessions.end()) return;
    
    auto session = it->second;
    
    // Filling the buffers with zero while retaining frame dimensions publishes
    // a synthetic black frame. Invalidate metadata instead; callers then get
    // nullptr until Desktop Duplication supplies a real fresh frame.
    session->frame_width = 0;
    session->frame_height = 0;
    session->shared_frame.data = nullptr;
    session->shared_frame.data_size = 0;
    session->current_read_buffer.store(nullptr);
    session->last_capture_time = 0;
    
    DEBUG_PRINT("capture_clear_frame_cache: Frame buffers cleared");
}
