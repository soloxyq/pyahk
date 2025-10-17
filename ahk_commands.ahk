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
global CMD_SEND_KEY := 10
global CMD_SEND_SEQUENCE := 11
global CMD_SET_STATIONARY := 12
global CMD_SET_FORCE_MOVE_KEY := 13
global CMD_SET_FORCE_MOVE_STATE := 14
global CMD_SET_MANAGED_KEY_CONFIG := 15
global CMD_CLEAR_HOOKS := 16
global CMD_SET_FORCE_MOVE_REPLACEMENT_KEY := 17
