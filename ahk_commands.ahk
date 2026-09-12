; ===============================================================================
; AHK命令协议定义
; 与Python端的命令ID保持一致
; ===============================================================================

; 命令ID定义
global CMD_PING := 1
global CMD_SET_TARGET := 2
global CMD_ACTIVATE := 3
global CMD_ENQUEUE := 4
global CMD_CLEAR_QUEUE := 5
global CMD_PAUSE := 6
global CMD_RESUME := 7
global CMD_HOOK_REGISTER := 8
global CMD_HOOK_UNREGISTER := 9
global CMD_SEND_KEY := 10       ; 保留协议墓碑；WM_COPYDATA 刻意不处理
global CMD_SEND_SEQUENCE := 11  ; 保留协议墓碑；实际统一走 CMD_ENQUEUE
global CMD_SET_STATIONARY := 12
global CMD_SET_FORCE_MOVE_KEY := 13
global CMD_SET_FORCE_MOVE_STATE := 14
global CMD_SET_MANAGED_KEY_CONFIG := 15
global CMD_CLEAR_HOOKS := 16
global CMD_SET_FORCE_MOVE_REPLACEMENT_KEY := 17
global CMD_SET_PYTHON_WINDOW_STATE := 18
global CMD_BATCH_UPDATE_CONFIG := 19
global CMD_SET_SEND_MODE := 20
global CMD_SET_FORCE_MOVE_PASSTHROUGH_KEYS := 21
global CMD_SET_MACRO_STEPS := 22
global CMD_START_MACRO := 23
global CMD_STOP_MACRO := 24
global CMD_SET_SKILL_HOLD_KEYS := 25
global CMD_SET_ACCEPTING_ACTIONS := 26
global CMD_SHUTDOWN := 27
global CMD_RESET_RUNTIME := 28
global CMD_SET_RUNTIME_OWNER := 29
