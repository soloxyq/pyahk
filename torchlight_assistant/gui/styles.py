#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI样式模块"""


def get_modern_style() -> str:
    """获取现代化样式表"""
    return """
        QMainWindow {
            background-color: #2b2b2b;
            color: #ffffff;
            font-family: 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
            font-size: 9pt;
        }
        
        QWidget {
            background-color: #2b2b2b;
            color: #ffffff;
        }
        
        QGroupBox {
            font-weight: bold;
            border: 1px solid #666666;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 12px;
            background-color: #353535;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
            color: #ffffff;
        }
        
        QPushButton {
            background-color: #4a90e2;
            border: none;
            border-radius: 4px;
            padding: 6px 12px;
            color: white;
            font-weight: bold;
            min-height: 16px;
        }
        
        QPushButton:hover {
            background-color: #357abd;
        }
        
        QPushButton:pressed {
            background-color: #2968a3;
        }
        
        QComboBox {
            border: 1px solid #666666;
            border-radius: 3px;
            padding: 3px 6px;
            background-color: #404040;
            min-height: 18px;
        }
        
        QComboBox:focus {
            border-color: #4a90e2;
        }
        
        QLineEdit {
            border: 1px solid #666666;
            border-radius: 3px;
            padding: 4px;
            background-color: #404040;
        }
        
        QLineEdit:focus {
            border-color: #4a90e2;
        }
        
        QSpinBox {
            border: 1px solid #666666;
            border-radius: 3px;
            padding: 3px;
            background-color: #404040;
            min-height: 18px;
        }
        
        QSpinBox:focus {
            border-color: #4a90e2;
        }
        
        QCheckBox {
            spacing: 5px;
        }
        
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 2px;
            border: 1px solid #666666;
            background-color: #404040;
        }
        
        QCheckBox::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }
        
        QRadioButton::indicator {
            width: 16px;
            height: 16px;
            border-radius: 8px;
            border: 1px solid #666666;
            background-color: #404040;
        }
        
        QRadioButton::indicator:checked {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }
        
        QLabel {
            color: #ffffff;
        }
        
        QTabWidget::pane {
            border: 1px solid #666666;
            border-radius: 6px;
            background-color: #353535;
        }
        
        QTabWidget::tab-bar {
            alignment: left;
        }
        
        QTabBar::tab {
            background-color: #404040;
            border: 1px solid #666666;
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 8px 16px;
            margin-right: 2px;
            color: #ffffff;
            font-weight: bold;
        }
        
        QTabBar::tab:selected {
            background-color: #4a90e2;
            border-color: #4a90e2;
        }
        
        QTabBar::tab:hover {
            background-color: #357abd;
        }
        
        QTextEdit {
            border: 1px solid #666666;
            border-radius: 3px;
            padding: 4px;
            background-color: #404040;
        }
        
        QTextEdit:focus {
            border-color: #4a90e2;
        }
        """