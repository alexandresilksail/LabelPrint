"""SQLite schema 与预置数据。

时间统一以 UTC ISO 8601 字符串存储，展示层再转换为本地时区。
编码状态机：generated -> allocated -> used；任一非终态可 -> invalidated。
invalidated 记录永久保留，不得重新分配（barcode_value UNIQUE 兜底）。
"""

SCHEMA_VERSION = 1

VALID_RECORD_STATUSES = ("generated", "allocated", "used", "invalidated")
VALID_RANGE_STATUSES = ("active", "exhausted", "disabled")
VALID_SOURCE_TYPES = ("internal", "gs1_authorized", "import")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS barcode_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode_value     TEXT NOT NULL UNIQUE,
    barcode_type      TEXT NOT NULL,
    source_type       TEXT NOT NULL DEFAULT 'internal',
    allocation_status TEXT NOT NULL DEFAULT 'generated',
    product_name      TEXT,
    notes             TEXT,
    range_id          INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    invalidated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_barcode_records_type    ON barcode_records(barcode_type);
CREATE INDEX IF NOT EXISTS idx_barcode_records_status  ON barcode_records(allocation_status);
CREATE INDEX IF NOT EXISTS idx_barcode_records_created ON barcode_records(created_at);

CREATE TABLE IF NOT EXISTS numbering_ranges (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    range_name              TEXT NOT NULL,
    barcode_type            TEXT NOT NULL,
    source_type             TEXT NOT NULL DEFAULT 'internal',
    prefix                  TEXT NOT NULL DEFAULT '',
    start_number            INTEGER NOT NULL,
    end_number              INTEGER NOT NULL,
    next_number             INTEGER NOT NULL,
    authorization_reference TEXT,
    range_status            TEXT NOT NULL DEFAULT 'active',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_numbering_ranges_status ON numbering_ranges(range_status);

CREATE TABLE IF NOT EXISTS print_templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name  TEXT NOT NULL UNIQUE,
    paper_width    REAL NOT NULL,
    paper_height   REAL NOT NULL,
    label_width    REAL NOT NULL,
    label_height   REAL NOT NULL,
    rows           INTEGER NOT NULL DEFAULT 1,
    columns        INTEGER NOT NULL DEFAULT 1,
    margins        TEXT NOT NULL DEFAULT '{"top":5,"right":5,"bottom":5,"left":5}',
    horizontal_gap REAL NOT NULL DEFAULT 3,
    vertical_gap   REAL NOT NULL DEFAULT 3,
    orientation    TEXT NOT NULL DEFAULT 'portrait',
    barcode_height REAL NOT NULL DEFAULT 15,
    show_text      INTEGER NOT NULL DEFAULT 1,
    copies         INTEGER NOT NULL DEFAULT 1,
    offset_x       REAL NOT NULL DEFAULT 0,
    offset_y       REAL NOT NULL DEFAULT 0,
    builtin        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type   TEXT NOT NULL,
    barcode_value    TEXT,
    operation_time   TEXT NOT NULL,
    operation_result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_logs_value ON operation_logs(barcode_value);
CREATE INDEX IF NOT EXISTS idx_operation_logs_time  ON operation_logs(operation_time);
"""


def _grid(label_w, label_h, paper_w=210.0, paper_h=297.0, gap=3.0, margin=5.0):
    """按 A4 纸估算行列数（仅作预置模板初值，用户可改）。"""
    cols = max(int((paper_w - 2 * margin + gap) // (label_w + gap)), 1)
    rows = max(int((paper_h - 2 * margin + gap) // (label_h + gap)), 1)
    return rows, cols


def _default_templates():
    """规格 6.2 预置模板：30x20 / 40x25 / 50x30 / 60x40 mm。"""
    out = []
    for name, lw, lh in [
        ("标签 30x20 mm", 30.0, 20.0),
        ("标签 40x25 mm", 40.0, 25.0),
        ("标签 50x30 mm", 50.0, 30.0),
        ("标签 60x40 mm", 60.0, 40.0),
    ]:
        rows, cols = _grid(lw, lh)
        out.append(
            {
                "template_name": name,
                "paper_width": 210.0,
                "paper_height": 297.0,
                "label_width": lw,
                "label_height": lh,
                "rows": rows,
                "columns": cols,
                "builtin": 1,
            }
        )
    return out
