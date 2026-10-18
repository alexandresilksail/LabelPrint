"""UI 主题与样式常量。"""

# 主色（与品牌一致的蓝紫系）
PRIMARY = "#4F6BED"
PRIMARY_DARK = "#3B53C4"
PRIMARY_LIGHT = "#EEF1FD"
SUCCESS = "#1F9D55"
WARNING = "#D97706"
DANGER = "#DC2626"
TEXT = "#1F2937"
TEXT_MUTED = "#6B7280"
BORDER = "#E5E7EB"
BG = "#F7F8FA"
CARD = "#FFFFFF"

FONT_FAMILY = "Segoe UI, Microsoft YaHei, sans-serif"

STYLESHEET = f"""
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QFrame#Card, QFrame#Panel {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    border-color: {PRIMARY};
    color: {PRIMARY};
}}
QPushButton#Primary {{
    background: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: white;
}}
QPushButton#Primary:hover {{
    background: {PRIMARY_DARK};
}}
QPushButton#Danger {{
    color: {DANGER};
    border-color: {DANGER};
}}
QPushButton:disabled {{
    color: #9CA3AF;
    border-color: {BORDER};
}}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    background: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {PRIMARY};
}}
QTableWidget {{
    background: white;
    alternate-background-color: #F9FAFB;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QHeaderView::section {{
    background: {PRIMARY_LIGHT};
    color: {TEXT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-weight: 600;
}}
QListWidget#Nav {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    outline: 0;
}}
QListWidget#Nav::item {{
    padding: 10px 12px;
    border-radius: 8px;
    margin: 2px 6px;
}}
QListWidget#Nav::item:selected {{
    background: {PRIMARY};
    color: white;
}}
QLabel#Title {{
    font-size: 18px;
    font-weight: 700;
}}
QLabel#Subtitle {{
    color: {TEXT_MUTED};
}}
QLabel#StatValue {{
    font-size: 24px;
    font-weight: 700;
    color: {PRIMARY};
}}
"""

STATUS_COLORS = {
    "generated": PRIMARY,
    "allocated": SUCCESS,
    "used": TEXT_MUTED,
    "invalidated": DANGER,
    "active": SUCCESS,
    "exhausted": WARNING,
    "pending": WARNING,
    "paid": SUCCESS,
    "confirmed": SUCCESS,
    "failed": DANGER,
    "canceled": TEXT_MUTED,
    "refunded": WARNING,
    "trialing": PRIMARY,
    "expired": DANGER,
    "suspended": DANGER,
    "past_due": WARNING,
}
