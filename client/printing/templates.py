"""标签模板模型与版面几何计算。

所有尺寸单位统一为毫米（mm）。模板即数据类，可与 print_templates 表
双向转换（from_dict / to_dict）。
"""

from dataclasses import dataclass, field, asdict
import json

from ..database.schema import VALID_ORIENTATIONS

DEFAULT_MARGINS = {"top": 5.0, "right": 5.0, "bottom": 5.0, "left": 5.0}

# 规格 6.2 预置模板
PRESET_TEMPLATES = [
    {"template_name": "标签 30x20 mm", "label_width": 30.0, "label_height": 20.0},
    {"template_name": "标签 40x25 mm", "label_width": 40.0, "label_height": 25.0},
    {"template_name": "标签 50x30 mm", "label_width": 50.0, "label_height": 30.0},
    {"template_name": "标签 60x40 mm", "label_width": 60.0, "label_height": 40.0},
]

# GS1 条码缩放规范（EAN/UPC）：标称模块 0.33mm，允许 80%~200%
GS1_MODULE_MM = 0.33
GS1_MIN_MODULE_MM = GS1_MODULE_MM * 0.8   # 0.264
GS1_MAX_MODULE_MM = GS1_MODULE_MM * 2.0   # 0.66


@dataclass
class LabelTemplate:
    template_name: str = "自定义模板"
    paper_width: float = 210.0        # A4 宽 mm
    paper_height: float = 297.0       # A4 高 mm
    label_width: float = 40.0
    label_height: float = 25.0
    rows: int = 10
    columns: int = 5
    margins: dict = field(default_factory=lambda: dict(DEFAULT_MARGINS))
    horizontal_gap: float = 3.0
    vertical_gap: float = 3.0
    orientation: str = "portrait"
    barcode_height: float = 15.0      # 条码区高度 mm
    show_text: bool = True
    copies: int = 1
    offset_x: float = 0.0
    offset_y: float = 0.0
    builtin: bool = False
    # 数据库主键（来自 print_templates.id）；未持久化时为 None
    id: int | None = None

    # ---------- 序列化 ----------
    def to_dict(self):
        d = asdict(self)
        d.pop("id", None)  # id 由数据库自增生成，不参与 INSERT/UPDATE
        d["margins"] = json.dumps(self.margins, ensure_ascii=False)
        d["show_text"] = 1 if self.show_text else 0
        d["builtin"] = 1 if self.builtin else 0
        return d

    @classmethod
    def from_dict(cls, data):
        d = dict(data)
        margins = d.get("margins")
        if isinstance(margins, str):
            try:
                margins = json.loads(margins)
            except (ValueError, TypeError):
                margins = dict(DEFAULT_MARGINS)
        d["margins"] = {**DEFAULT_MARGINS, **(margins or {})}
        d["show_text"] = bool(d.get("show_text", 1))
        d["builtin"] = bool(d.get("builtin", 0))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    # ---------- 几何 ----------
    def usable_width(self):
        return self.paper_width - self.margins["left"] - self.margins["right"]

    def usable_height(self):
        return self.paper_height - self.margins["top"] - self.margins["bottom"]

    def grid_size_mm(self):
        """整个标签网格的宽高（mm）。"""
        w = self.columns * self.label_width + (self.columns - 1) * self.horizontal_gap
        h = self.rows * self.label_height + (self.rows - 1) * self.vertical_gap
        return w, h

    def validate(self):
        """返回 (errors, warnings)。errors 阻止保存；warnings 提示但允许。"""
        errors, warnings = [], []
        if not self.template_name or not self.template_name.strip():
            errors.append("模板名称不能为空")
        for name, val in (
            ("纸张宽度", self.paper_width), ("纸张高度", self.paper_height),
            ("标签宽度", self.label_width), ("标签高度", self.label_height),
            ("条码高度", self.barcode_height),
        ):
            if val <= 0:
                errors.append(f"{name}必须为正数")
        if self.label_width > self.paper_width:
            errors.append("标签宽度超出纸张宽度")
        if self.label_height > self.paper_height:
            errors.append("标签高度超出纸张高度")
        if self.rows < 1 or self.columns < 1:
            errors.append("每页行数/列数必须 >= 1")
        if self.copies < 1:
            errors.append("打印份数必须 >= 1")
        if self.horizontal_gap < 0 or self.vertical_gap < 0:
            errors.append("标签间距不能为负")
        if self.orientation not in VALID_ORIENTATIONS:
            errors.append(f"非法打印方向: {self.orientation}")
        gw, gh = self.grid_size_mm()
        if gw > self.usable_width() + 1e-6:
            warnings.append(
                f"标签网格宽度 {gw:.1f}mm 超出页面可用宽度 "
                f"{self.usable_width():.1f}mm，右侧标签将越界"
            )
        if gh > self.usable_height() + 1e-6:
            warnings.append(
                f"标签网格高度 {gh:.1f}mm 超出页面可用高度 "
                f"{self.usable_height():.1f}mm，下方标签将越界"
            )
        if self.barcode_height > self.label_height:
            warnings.append("条码高度大于标签高度，将按标签高度截断")
        return errors, warnings

    def label_positions(self):
        """生成全部标签左上角坐标（mm，页面原点为左上角）。

        越界标签也返回（由调用方结合警告处理），保证网格完整可见。
        """
        positions = []
        for row in range(self.rows):
            for col in range(self.columns):
                x = self.margins["left"] + self.offset_x + \
                    col * (self.label_width + self.horizontal_gap)
                y = self.margins["top"] + self.offset_y + \
                    row * (self.label_height + self.vertical_gap)
                positions.append((row, col, x, y))
        return positions


def preset_templates():
    """返回规格 6.2 预置模板实例列表（A4 纸，自动估算行列数）。"""
    out = []
    for p in PRESET_TEMPLATES:
        t = LabelTemplate(
            template_name=p["template_name"],
            label_width=p["label_width"],
            label_height=p["label_height"],
            builtin=True,
        )
        # 按 A4 估算行列数
        gw = 0
        cols = 1
        while True:
            w = cols * t.label_width + (cols - 1) * t.horizontal_gap
            if w > t.usable_width() or cols > 50:
                break
            cols += 1
        cols -= 1
        rows = 1
        while True:
            h = rows * t.label_height + (rows - 1) * t.vertical_gap
            if h > t.usable_height() or rows > 50:
                break
            rows += 1
        rows -= 1
        t.rows = max(rows, 1)
        t.columns = max(cols, 1)
        out.append(t)
    return out
