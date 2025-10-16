;===================================================================================
; 暗黑破坏神IV - DH陷阱宏 - 优化版 (AHK v2)
;===================================================================================

;===================================================================================
; 基础设置 - 性能优化
;===================================================================================
#Requires AutoHotkey v2.0
#SingleInstance Force ;只能启动一个ahk程序实例，防止重复启动
FileEncoding "UTF-8"
ListLines false
ProcessSetPriority "A"
SetKeyDelay -1, -1
SetMouseDelay -1
SetDefaultMouseSpeed 0
SendMode "Input"

#Include "Gdip_All.ahk"

;===================================================================================
; 用户配置区 - 所有可配置项集中在此处
;===================================================================================

; 技能启用设置 (1=启用, 0=禁用)
global USER_SKILL_ONE_ENABLED := 1      ; 1号技能(C键)
global USER_SKILL_TWO_ENABLED := 0      ; 2号技能(2键)
global USER_SKILL_THREE_ENABLED := 1    ; 3号技能(3键)
global USER_SKILL_FOUR_ENABLED := 1     ; 4号技能(4键)
global USER_SKILL_LEFT_ENABLED := 0     ; 左键技能
global USER_SKILL_RIGHT_ENABLED := 0    ; 右键技能
global USER_SKILL_DANCE_ENABLED := 1    ; D键技能

; 技能施放方式设置 (1=checkbuff检测CD, 0=timer定时发送)
global USER_SKILL_ONE_USE_CHECKBUFF := 1    ; 1号技能使用CD检测
global USER_SKILL_TWO_USE_CHECKBUFF := 0    ; 2号技能使用CD检测
global USER_SKILL_THREE_USE_CHECKBUFF := 0  ; 3号技能使用CD检测
global USER_SKILL_FOUR_USE_CHECKBUFF := 1   ; 4号技能使用CD检测
global USER_SKILL_LEFT_USE_CHECKBUFF := 0   ; 左键技能使用CD检测
global USER_SKILL_RIGHT_USE_CHECKBUFF := 1  ; 右键技能使用CD检测
global USER_SKILL_DANCE_USE_CHECKBUFF := 0  ; D键技能使用CD检测

; 技能定时器间隔设置 (毫秒)
global USER_SKILL_ONE_TIMER := 110     ; 1号技能间隔
global USER_SKILL_TWO_TIMER := 130      ; 2号技能间隔
global USER_SKILL_THREE_TIMER := 150    ; 3号技能间隔
global USER_SKILL_FOUR_TIMER := 160     ; 4号技能间隔
global USER_SKILL_LEFT_TIMER := 0       ; 左键技能间隔
global USER_SKILL_RIGHT_TIMER := 0      ; 右键技能间隔
global USER_SKILL_DANCE_TIMER := 45     ; D键技能间隔
global USER_MOUSE_LBUTTON_TIMER := 75   ; 左键点击间隔
global USER_YAO_TIMER := 0           ; 药水使用间隔

; 技能序列设置
global USER_SKILL_SEQUENCE := "1"  ; 默认技能序列配置，用逗号分隔

; 音效文件路径
global SOUND_START := "E:\danger\hello.mp3"      ; 启动音效
global SOUND_STOP := "E:\danger\goodbye.mp3"     ; 停止音效
global SOUND_RESUME := "E:\danger\resume.mp3"    ; 恢复音效
global SOUND_PAUSE := "E:\danger\pause.mp3"      ; 暂停音效

;===================================================================================
; 全局变量 - 请勿手动修改
;===================================================================================

; 坐标模式设置
global coord := "Screen"
CoordMode "ToolTip", coord
CoordMode "Pixel", coord
CoordMode "Mouse", coord
CoordMode "Caret", coord
CoordMode "Menu", coord

; 控制变量
global masterFlag := 0      ; 宏控制变量 0=关闭宏 1=启动宏
global clickFlag := 0       ; 控制左键连点的变量 0=不点击 1=点击
global buffFlag := 0        ; 0=白人 1=黑人
global shiftClickFlag := 0  ; 用于切换 Shift+左键 和 左键
global RButtonStatu := 0    ; 右键状态
global trigger_flag := 1    ; 触发标志
global qToggle := 0         ; Q键切换

; 技能图片路径
global global_skillone_path := A_ScriptDir . "\images\dh\skillone.bmp"
global global_skilltwo_path := A_ScriptDir . "\images\dh\skilltwo.bmp"
global global_skillthree_path := A_ScriptDir . "\images\dh\skillthree.bmp"
global global_skillfour_path := A_ScriptDir . "\images\dh\skillfour.bmp"
global global_skillleft_path := A_ScriptDir . "\images\dh\skillleft.bmp"
global global_skillright_path := A_ScriptDir . "\images\dh\skillright.bmp"
global global_skilldance_path := A_ScriptDir . "\images\dh\skilldance.bmp"
global global_confirm_yes := A_ScriptDir . "\images\confirmyes.bmp"

; 技能设置 - 从用户配置区加载
global global_skill_changed := 1
global global_skill_one := USER_SKILL_ONE_ENABLED
global global_skill_two := USER_SKILL_TWO_ENABLED
global global_skill_three := USER_SKILL_THREE_ENABLED
global global_skill_four := USER_SKILL_FOUR_ENABLED
global global_skill_left := USER_SKILL_LEFT_ENABLED
global global_skill_right := USER_SKILL_RIGHT_ENABLED
global global_skill_dance := USER_SKILL_DANCE_ENABLED

; 技能定时器 - 从用户配置区加载
global global_skillone_timer := USER_SKILL_ONE_TIMER
global global_skilltwo_timer := USER_SKILL_TWO_TIMER
global global_skillthree_timer := USER_SKILL_THREE_TIMER
global global_skillfour_timer := USER_SKILL_FOUR_TIMER
global global_skillleft_timer := USER_SKILL_LEFT_TIMER
global global_skillright_timer := USER_SKILL_RIGHT_TIMER
global global_skilldance_timer := USER_SKILL_DANCE_TIMER
global global_mouseLButton_timer := USER_MOUSE_LBUTTON_TIMER
global global_yao_timer := USER_YAO_TIMER

; 技能施放方式 - 从用户配置区加载
global global_skill_one_use_checkbuff := USER_SKILL_ONE_USE_CHECKBUFF
global global_skill_two_use_checkbuff := USER_SKILL_TWO_USE_CHECKBUFF
global global_skill_three_use_checkbuff := USER_SKILL_THREE_USE_CHECKBUFF
global global_skill_four_use_checkbuff := USER_SKILL_FOUR_USE_CHECKBUFF
global global_skill_left_use_checkbuff := USER_SKILL_LEFT_USE_CHECKBUFF
global global_skill_right_use_checkbuff := USER_SKILL_RIGHT_USE_CHECKBUFF
global global_skill_dance_use_checkbuff := USER_SKILL_DANCE_USE_CHECKBUFF

; 技能颜色和坐标
global global_skillone_noworkColor := 0x141006
global global_skilltwo_noworkColor := 0x141006
global global_skillthree_noworkColor := 0x141006
global global_skillfour_noworkColor := 0x141006
global global_skillleft_noworkColor := 0x141006
global global_skillright_noworkColor := 0x141006

global global_skill_initonce := 0
global global_skill_oneX := 0, global_skill_oneY := 0
global global_skill_twoX := 0, global_skill_twoY := 0
global global_skill_threeX := 0, global_skill_threeY := 0
global global_skill_fourX := 0, global_skill_fourY := 0
global global_skill_leftX := 0, global_skill_leftY := 0
global global_skill_rightX := 0, global_skill_rightY := 0
global global_skill_danceX := 0, global_skill_danceY := 0

; 按键队列
global queue := []  ; 按键队列
global maxQueueLength := 7  ; 队列的最大长度

; 技能序列相关变量
global skillSequence := []  ; 技能序列数组
global skillSequenceEnabled := 0  ; 是否启用技能序列
global skillSequenceIndex := 1  ; 当前执行到的序列索引
global skillSequenceConfig := USER_SKILL_SEQUENCE  ; 从用户配置加载技能序列

; 其他设置
SetTitleMatchMode 2  ; 设置窗口标题匹配模式
global pToken, healthWidth := 130, healthHeight := 149, MyPicture
global StatusText, MyGui

#HotIf WinActive("ahk_class Diablo IV Main Window Class")

; 激活游戏窗口
WinActivate "ahk_class Diablo IV Main Window Class"

GuiStart() {
    ; 创建状态显示 GUI 窗口
    global StatusText, MyGui
    MyGui := Gui("+LastFound +AlwaysOnTop -Caption +ToolWindow +E0x20")
    MyGui.BackColor := "000000"
    MyGui.SetFont "s14 bold", "微软雅黑"
    StatusText := MyGui.Add("Text", "cLime", "你好，世界！")
    MyGui.Show "x960 y540 NoActivate"
    WinSetTransColor "000000", MyGui
}

; 错误恢复函数 - 尝试恢复宏的正常运行
RecoverFromError() {
    global masterFlag, clickFlag, buffFlag, RButtonStatu, trigger_flag, qToggle, queue, SOUND_STOP

    ; 停止所有定时器
    SetTimer MouseLButton, 0
    SetTimer YaoButtonTimer, 0
    SetTimer CheckBuffSeqLeft, 0
    SetTimer CheckBuffSeqRight, 0
    SetTimer CheckBuffSeqDance, 0
    SetTimer CheckBuffSeq01, 0
    SetTimer CheckBuffSeq02, 0
    SetTimer CheckBuffSeq03, 0
    SetTimer CheckBuffSeq04, 0
    SetTimer SendKey1, 0
    SetTimer SendKey2, 0
    SetTimer SendKey3, 0
    SetTimer SendKey4, 0
    SetTimer SendKeyLeft, 0
    SetTimer SendKeyRight, 0
    SetTimer SendKeyDance, 0
    SetTimer ExecuteSkillSequence, 0
    SetTimer ProcessQueue, 0

    ; 重置状态变量
    masterFlag := 0
    clickFlag := 0
    buffFlag := 0
    RButtonStatu := 0
    trigger_flag := 0
    qToggle := 0
    queue := []

    ; 更新GUI状态
    if (StatusText && WinExist(MyGui.Hwnd)) {
        StatusText.Value := "错误已恢复，请重新启动宏"
    }

    ; 显示错误提示
    MsgBox "发生严重错误，宏已停止运行！请重新启动宏。", "错误", 16

    ; 播放提示音
    SoundPlay SOUND_STOP
}

; 按键添加到队列的函数
;===================================================================================
; 按键队列处理函数
;===================================================================================

; 添加按键到队列
AddToQueue(key, priority := false) {
    global queue, maxQueueLength

/*     ; 如果队列长度超出限制，清空队列
    if (queue.Length >= maxQueueLength) {
        queue := []  ; 清空队列
    } */

    ; 先检查队首是否相同
    if (queue.Length > 0 && queue[1] = key) {
        return  ; 如果队首已经是相同按键，直接返回
    }

    ; 优先级按键直接插入队首
    if (priority) {
        queue.InsertAt(1, key)
    } else {
        ; 普通按键从队列第二个位置开始查找重复
        loop queue.Length - 1 {
            if (queue[A_Index + 1] = key) {
                return  ; 如果找到重复按键，直接返回
            }
        }
        queue.Push(key)  ; 没有重复则添加到队尾
    }
}

; 按键处理函数，集中处理队列中的按键
ProcessQueue() {
    global queue, masterFlag, buffFlag, shiftClickFlag

    ; 如果宏未启动或不在黑人模式，不处理队列
    if (!masterFlag || !buffFlag) {
        return
    }

    ; 检查A键是否按下
    forceMoveFlag := GetKeyState("a", "P")
    if (queue.Length = 0) {
        return
    }

    key := queue.RemoveAt(1)
    if (key == "2") {
        Sleep 25
    }    
    ; 如果A键按下，LButton和+LButton都替换为f
    if (forceMoveFlag) {
        if (key = "LButton" or key = "+LButton" or key = "+{Click}" or key = "{Click}" or key = "d" or key = "2") {
            key := "f"
        }
    }

    if (key = "delay_50") {
        Sleep 50
    } else if (shiftClickFlag and key != "f") {
        SendInput "+{" key "}"
    } else {
        SendInput "{" key "}"
    }

    if (key == "2") {
        Sleep 50
    }
}

init() {
    global
    if (global_skill_one = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skillone_path)) {
            global_skill_oneX := FoundX
            global_skill_oneY := FoundY
        } else {
            return 0
        }
        global_skillone_noworkColor := PixelGetColor(777, 968)
    }
    if (global_skill_two = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skilltwo_path)) {
            global_skill_twoX := FoundX
            global_skill_twoY := FoundY
        } else {
            return 0
        }
        global_skilltwo_noworkColor := PixelGetColor(840, 968)
    }
    if (global_skill_three = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skillthree_path)) {
            global_skill_threeX := FoundX
            global_skill_threeY := FoundY
        } else {
            return 0
        }
        global_skillthree_noworkColor := PixelGetColor(903, 968)
    }
    if (global_skill_four = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skillfour_path)) {
            global_skill_fourX := FoundX
            global_skill_fourY := FoundY
        } else {
            return 0
        }
        global_skillfour_noworkColor := PixelGetColor(966, 968)
    }
    if (global_skill_left = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skillleft_path)) {
            global_skill_leftX := FoundX
            global_skill_leftY := FoundY
        } else {
            return 0
        }
        global_skillleft_noworkColor := PixelGetColor(1029, 968)
    }
    if (global_skill_right = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skillright_path)) {
            global_skill_rightX := FoundX
            global_skill_rightY := FoundY
        } else {
            return 0
        }
        global_skillright_noworkColor := PixelGetColor(1092, 968)
    }
    if (global_skill_dance = 1) {
        if (ImageSearch(&FoundX, &FoundY, 0, 0, 1920, 1080, global_skilldance_path)) {
            global_skill_danceX := FoundX
            global_skill_danceY := FoundY
        } else {
            return 0
        }
    }

    global_skill_initonce := 1
    return 1
}

checkColorBlack() {
    colorSuperFlagC := PixelGetColor(858, 1032)
    return (colorSuperFlagC = 0x1D1C1B OR colorSuperFlagC = 0x1C1B1A)
}

checkBuffOneNotWorking() {
    global global_skillone_noworkColor
    colorSuperFlagC := PixelGetColor(777, 968)
    return (colorSuperFlagC = global_skillone_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

checkBuffTwoNotWorking() {
    global global_skilltwo_noworkColor
    colorSuperFlagC := PixelGetColor(840, 968)
    return (colorSuperFlagC = global_skilltwo_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

checkBuffThreeNotWorking() {
    global global_skillthree_noworkColor
    colorSuperFlagC := PixelGetColor(903, 968)
    return (colorSuperFlagC = global_skillthree_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

checkBuffFourNotWorking() {
    global global_skillfour_noworkColor
    colorSuperFlagC := PixelGetColor(966, 968)
    return (colorSuperFlagC = global_skillfour_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

checkBuffLeftNotWorking() {
    global global_skillleft_noworkColor
    colorSuperFlagC := PixelGetColor(1029, 968)
    return (colorSuperFlagC = global_skillleft_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

checkBuffRightNotWorking() {
    global global_skillright_noworkColor
    colorSuperFlagC := PixelGetColor(1092, 968)
    return (colorSuperFlagC = global_skillright_noworkColor OR colorSuperFlagC = 0x1C1A01)
}

convertRGB(color) {
    vblue := (color & 0xFF)
    vgreen := ((color & 0xFF00) >> 8)
    vred := ((color & 0xFF0000) >> 16)
    return [vred, vgreen, vblue]
}

checkRgbLike(pixelX, pixelY, expectColor) {
    color := PixelGetColor(pixelX, pixelY)
    c1 := convertRGB(color)
    c2 := convertRGB(expectColor)
    return Sqrt((c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2 + (c1[3] - c2[3]) ** 2)
}

checkFullClickResource() {
    isLike := checkRgbLike(993, 923, 0x48cfde)
    return isLike < 12
}

checkXingYanResource() {
    isLike := checkRgbLike(1385, 1031, 0x6a124a)
    return isLike < 12
}

checkMsgboxConfirm() {
    SendInput "{Enter}"
}

checkSkillOneCD() {
    global global_skill_oneX, global_skill_oneY, global_skillone_path
    tempx := global_skill_oneX + 64, tempy := global_skill_oneY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_oneX, global_skill_oneY, tempx, tempy, global_skillone_path)
}

checkSkillTwoCD() {
    global global_skill_twoX, global_skill_twoY, global_skilltwo_path
    tempx := global_skill_twoX + 64, tempy := global_skill_twoY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_twoX, global_skill_twoY, tempx, tempy, global_skilltwo_path)
}

checkSkillThreeCD() {
    global global_skill_threeX, global_skill_threeY, global_skillthree_path
    tempx := global_skill_threeX + 64, tempy := global_skill_threeY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_threeX, global_skill_threeY, tempx, tempy, global_skillthree_path
    )
}

checkSkillFourCD() {
    global global_skill_fourX, global_skill_fourY, global_skillfour_path
    tempx := global_skill_fourX + 64, tempy := global_skill_fourY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_fourX, global_skill_fourY, tempx, tempy, global_skillfour_path)
}

checkSkillLeftCD() {
    global global_skill_leftX, global_skill_leftY, global_skillleft_path
    tempx := global_skill_leftX + 64, tempy := global_skill_leftY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_leftX, global_skill_leftY, tempx, tempy, global_skillleft_path)
}

checkSkillRightCD() {
    global global_skill_rightX, global_skill_rightY, global_skillright_path
    tempx := global_skill_rightX + 64, tempy := global_skill_rightY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_rightX, global_skill_rightY, tempx, tempy, global_skillright_path
    )
}

checkSkillDanceCD() {
    global global_skill_danceX, global_skill_danceY, global_skilldance_path
    tempx := global_skill_danceX + 64, tempy := global_skill_danceY + 64
    return ImageSearch(&FoundX, &FoundY, global_skill_danceX, global_skill_danceY, tempx, tempy, global_skilldance_path
    )
}

$F8:: {
    global skillSequenceEnabled, masterFlag, clickFlag, buffFlag, shiftClickFlag
    global global_skill_one_use_checkbuff, global_skill_two_use_checkbuff, global_skill_three_use_checkbuff,
        global_skill_four_use_checkbuff
    global global_skill_left_use_checkbuff, global_skill_right_use_checkbuff, global_skill_dance_use_checkbuff
    global global_skill_initonce, global_skill_changed, pToken, SOUND_START, SOUND_STOP, StatusText, MyGui
    if (global_skill_initonce = 0) {
        if (global_skill_changed = 1) {
            if !pToken := Gdip_Startup() {
                MsgBox "gdiplus error!, Gdiplus failed to start. Please ensure you have gdiplus on your system", , 48
                return
            }
            snap := Gdip_BitmapFromScreen("792|996|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skillone.bmp")) {
                MsgBox "skillone_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("843|982|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skilltwo.bmp")) {
                MsgBox "skilltwo_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("918|996|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skillthree.bmp")) {
                MsgBox "skillthree_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("982|996|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skillfour.bmp")) {
                MsgBox "skillfour_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("1044|996|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skillleft.bmp")) {
                MsgBox "skillleft_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("1106|996|18|18")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skillright.bmp")) {
                MsgBox "skillright_path save failed"
            }
            Gdip_DisposeImage(snap)
            snap := Gdip_BitmapFromScreen("1237|900|6|6")
            if (0 != Gdip_SaveBitmapToFile(snap, A_ScriptDir . "\images\dh\skilldance.bmp")) {
                MsgBox "skilldance_path save failed"
            }
            Gdip_DisposeImage(snap)
            ; Gdip_Shutdown(pToken)
        }
        if (init() = 0) {
            MsgBox "initError"
            return
        }
    }
    if (masterFlag = 0) { ;开宏
        masterFlag := 1
        clickFlag := 0
        buffFlag := 0
        RButtonStatu := 0
        trigger_flag := 0
        qToggle := 0
        queue := []  ; 初始化队列
        shiftClickFlag := 0

        GuiStart()  ; 创建状态显示窗口

        ; 根据启动前选择的模式初始化
        if (skillSequenceEnabled = 1) {
            ; 技能序列模式
            InitSkillSequence()
            StatusText.Value := "关闭(序列)"
            SetTimer ExecuteSkillSequence, 0
            SetTimer ProcessQueue, 5
        } else {
            ; 普通模式
            StatusText.Value := "关闭"

            ; 设置基础定时器
            if (global_mouseLButton_timer > 0)
                SetTimer MouseLButton, global_mouseLButton_timer
            if (global_yao_timer > 0)
                SetTimer YaoButtonTimer, global_yao_timer

            ; 根据技能施放方式设置定时器
            if (global_skill_one = 1) {
                if (global_skill_one_use_checkbuff = 1)
                    SetTimer CheckBuffSeq01, 100
                else if (global_skillone_timer > 0)
                    SetTimer SendKey1, global_skillone_timer
            }
            if (global_skill_two = 1) {
                if (global_skill_two_use_checkbuff = 1)
                    SetTimer CheckBuffSeq02, 124
                else if (global_skilltwo_timer > 0)
                    SetTimer SendKey2, global_skilltwo_timer
            }
            if (global_skill_three = 1) {
                if (global_skill_three_use_checkbuff = 1)
                    SetTimer CheckBuffSeq03, 148
                else if (global_skillthree_timer > 0)
                    SetTimer SendKey3, global_skillthree_timer
            }
            if (global_skill_four = 1) {
                if (global_skill_four_use_checkbuff = 1)
                    SetTimer CheckBuffSeq04, 172
                else if (global_skillfour_timer > 0)
                    SetTimer SendKey4, global_skillfour_timer
            }
            if (global_skill_left = 1) {
                if (global_skill_left_use_checkbuff = 1)
                    SetTimer CheckBuffSeqLeft, 160
                else if (global_skillleft_timer > 0)
                    SetTimer SendKeyLeft, global_skillleft_timer
            }
            if (global_skill_right = 1) {
                if (global_skill_right_use_checkbuff = 1)
                    SetTimer CheckBuffSeqRight, 136
                else if (global_skillright_timer > 0)
                    SetTimer SendKeyRight, global_skillright_timer
            }
            if (global_skill_dance = 1) {
                if (global_skill_dance_use_checkbuff = 1)
                    SetTimer CheckBuffSeqDance, 100
                else if (global_skilldance_timer > 0)
                    SetTimer SendKeyDance, global_skilldance_timer
            }

            SetTimer ProcessQueue, 25
        }

        SoundPlay SOUND_START
    } else { ;关宏
        SetTimer MouseLButton, 0
        SetTimer YaoButtonTimer, 0
        SetTimer CheckBuffSeqLeft, 0
        SetTimer CheckBuffSeqRight, 0
        SetTimer CheckBuffSeqDance, 0
        SetTimer CheckBuffSeq01, 0
        SetTimer CheckBuffSeq02, 0
        SetTimer CheckBuffSeq03, 0
        SetTimer CheckBuffSeq04, 0
        SetTimer SendKey1, 0
        SetTimer SendKey2, 0
        SetTimer SendKey3, 0
        SetTimer SendKey4, 0
        SetTimer SendKeyLeft, 0
        SetTimer SendKeyRight, 0
        SetTimer SendKeyDance, 0
        SetTimer ExecuteSkillSequence, 0
        SetTimer ProcessQueue, 0
        clickFlag := 0
        buffFlag := 0
        masterFlag := 0
        RButtonStatu := 0
        trigger_flag := 0
        qToggle := 0
        queue := []
        if (MyGui && WinExist(MyGui.Hwnd)) {
            MyGui.Destroy()
        }
        SoundPlay SOUND_STOP
    }
}

$z:: {
    global masterFlag, clickFlag, buffFlag, RButtonStatu, trigger_flag, shiftClickFlag, qToggle, queue,
        skillSequenceEnabled, SOUND_RESUME, SOUND_PAUSE, StatusText
    if (masterFlag = 1) {
        if (clickFlag = 0) {
            clickFlag := 1
            buffFlag := 1
            RButtonStatu := 1
            trigger_flag := 1
            queue := []
            shiftClickFlag := 0

            if (skillSequenceEnabled = 1) {
                SetTimer ExecuteSkillSequence, 25
                SetTimer ProcessQueue, 5
                StatusText.Value := "RUN(序列)"
            } else {
                SetTimer MouseLButton, global_mouseLButton_timer
                SetTimer ShiftMouseLButton, 0
                SetTimer ProcessQueue, 25
                SetTimer YaoButtonTimer, global_yao_timer

                if (shiftClickFlag) {
                    StatusText.Value := "RUN(原地)"
                } else {
                    StatusText.Value := "RUN(移动)"
                }
            }
            SoundPlay SOUND_RESUME
        } else {
            clickFlag := 0
            buffFlag := 0
            RButtonStatu := 0
            trigger_flag := 0
            qToggle := 0
            SetTimer YaoButtonTimer, 0
            SetTimer MouseLButton, 0
            SetTimer ShiftMouseLButton, 0
            SetTimer ProcessQueue, 0
            SetTimer ExecuteSkillSequence, 0
            queue := []
            shiftClickFlag := 0
            StatusText.Value := "STOP"
            SoundPlay SOUND_PAUSE
        }
    } else {
        MsgBox "请先启动宏（按F8）！"
    }
}

/**
 * 检测资源状态
 * @returns {Boolean} - 资源是否充足
 */
IsResourceSufficient() {
    ;1920/2 + (2620 - 3840/2) * 0.5 = 1310
    ;1080/2 + (1865 - 2160/2) * 0.5 = 932
    ; 计算资源条检测点
    x := 1310
    y := 932
    color := GetPixelRGB(x, y)
    return (color.b > color.r + color.g)

}

GetPixelRGB(x, y) {
    try {
        color := PixelGetColor(x, y, "RGB")
        r := (color >> 16) & 0xFF
        g := (color >> 8) & 0xFF
        b := color & 0xFF
        return { r: r, g: g, b: b }
    } catch as err {
        return { r: 0, g: 0, b: 0 }  ; 失败时返回黑色
    }
}

release_source() {
    isLike := checkRgbLike(1310, 986, 0x191611)
    if (isLike < 15) {
        isLike := checkRgbLike(1307, 942, 0x0B0B08)
        if (isLike < 15)
            Click
        else
            Send "2"
    } else {
        Click
    }
}

CheckBuffSeqLeft() {
    if (buffFlag = 1) {
        if (checkSkillLeftCD() = 1) {
            AddToQueue("+LButton")
        }
    }
}

CheckBuffSeqRight() {
    if (buffFlag = 1) {
        if (checkBuffRightNotWorking() = 1) {
            if (checkSkillRightCD() = 1) {
                AddToQueue("RButton", false)
            }
        }
    }
}

CheckBuffSeqDance() {
    if (buffFlag = 1) {
        if (checkSkillDanceCD() = 1) {
            AddToQueue("d")
        }
    }
}

CheckBuffSeq01() {
    if (buffFlag = 1) {
        if (checkBuffOneNotWorking() = 1) {
            if (checkSkillOneCD() = 1) {
                AddToQueue("c")
            }
        }
    }
}

CheckBuffSeq02() {
    if (buffFlag = 1) {
        if (checkBuffTwoNotWorking() = 1) {
            if (checkSkillTwoCD() = 1) {
                AddToQueue("2")
            }
        }
    }
}

CheckBuffSeq03() {
    if (buffFlag = 1) {
        if (checkBuffThreeNotWorking() = 1) {
            if (checkSkillThreeCD() = 1) {
                AddToQueue("3", true)
            }
        }
    }
}

DelayedRButton() {
    AddToQueue("3", true)
    AddToQueue("RButton down")
}

CheckBuffSeq04() {
    if (buffFlag = 1) {
        if (checkBuffFourNotWorking() = 1) {
            if (checkSkillFourCD() = 1) {
                AddToQueue("4", true)
            }
        }
    }
}

SendKey1() {
    if (buffFlag = 1) {
        AddToQueue("c")
    }
}

SendKey2() {
    if (buffFlag = 1) {
        AddToQueue("2")
    }
}

SendKey3() {
    if (buffFlag = 1) {
        AddToQueue("3", true)
    }
}

SendKey4() {
    if (buffFlag = 1) {
        AddToQueue("4")
    }
}

SendKeyLeft() {
    if (buffFlag = 1) {
        AddToQueue("LButton")
    }
}

SendKeyRight() {
    if (buffFlag = 1) {
        AddToQueue("RButton")
    }
}

SendKeyDance() {
    if (buffFlag = 1) {
        AddToQueue("d")
    }
}

closeBlack() {
    clickFlag := 0
    buffFlag := 0
}

YaoButtonTimer() {
    if (clickFlag = 1) {
        AddToQueue("x")
    }
}

MouseLButton() {
    if (clickFlag = 1) {
        if (IsResourceSufficient())
            AddToQueue("2", true)
        else
            AddToQueue("LButton")
    }
}

ShiftMouseLButton() {
    AddToQueue("r")
}

~Enter:: {
    global buffFlag, clickFlag, trigger_flag, SOUND_PAUSE, StatusText
    buffFlag := 0
    clickFlag := 0
    trigger_flag := 0
    SoundPlay SOUND_PAUSE
}

HandleKeyPress(key) {
    global clickFlag, buffFlag, masterFlag
    if (masterFlag = 1 && clickFlag = 1) {
        oldClickFlag := clickFlag
        oldBuffFlag := buffFlag
        clickFlag := 0
        buffFlag := 0
        SetTimer ProcessQueue, 0
        Sleep 25
        SendInput "{" . key . "}"
        Sleep 25
        clickFlag := oldClickFlag
        buffFlag := oldBuffFlag
        SetTimer ProcessQueue, 25
    } else {
        SendInput "{" . key . "}"
    }
}

~XButton1:: {
    global clickFlag, shiftClickFlag, StatusText

    if (clickFlag = 1) {
        shiftClickFlag := !shiftClickFlag
        if (shiftClickFlag) {
            StatusText.Value := "RUN(原地)"
        } else {
            StatusText.Value := "RUN(移动)"
        }
    }
}

InitSkillSequence() {
    global skillSequence, skillSequenceIndex, skillSequenceConfig, USER_SKILL_SEQUENCE
    skillSequence := []

    local sequenceParts := StrSplit(skillSequenceConfig, ",")
    for _, skill in sequenceParts {
        if (skill != "") {
            skillSequence.Push(skill)
        }
    }

    if (skillSequence.Length < 1) {
        skillSequence.Push("1")
        skillSequence.Push("2")
        skillSequence.Push("3")
        skillSequence.Push("4")
        skillSequence.Push("LButton")
        skillSequenceConfig := USER_SKILL_SEQUENCE
    }

    skillSequenceIndex := 1
}

ExecuteSkillSequence() {
    global buffFlag, skillSequenceEnabled, skillSequence, skillSequenceIndex, masterFlag

    if (!masterFlag || buffFlag != 1 || skillSequenceEnabled != 1) {
        return
    }
    if (!skillSequence || skillSequence.Length < 1) {
        InitSkillSequence()
        return
    }

    currentSkill := skillSequence[skillSequenceIndex]
    AddToQueue(currentSkill, false)

    skillSequenceIndex += 1
    if (skillSequenceIndex > skillSequence.Length) {
        skillSequenceIndex := 1
    }
}

$F9:: {
    global skillSequenceEnabled, masterFlag
    if (masterFlag = 0) {
        skillSequenceEnabled := !skillSequenceEnabled
        if (skillSequenceEnabled = 1) {
            MsgBox "已切换到技能序列模式，启动宏后将按序列执行技能"
        } else {
            MsgBox "已切换到普通模式，启动宏后将使用常规技能释放逻辑"
        }
    }
    else {
        MsgBox "宏已启动，无法切换模式。请先关闭宏（F8），再切换模式。"
    }
}

#HotIf