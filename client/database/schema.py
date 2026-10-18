"""SQLite schema 与预置数据（v2）。

时间统一以 UTC ISO 8601 字符串存储，展示层再转换为本地时区。
编码状态机：generated -> allocated -> used；任一非终态可 -> invalidated。
invalidated 记录永久保留，不得重新分配（barcode_value UNIQUE 兜底）。

v2 变更（对应审计发现 F-02/F-03/F-06）：
- 全关键字段增加数据库层 CHECK 约束，防止 DAO 直写绕过业务规则；
- barcode_records 新增 authorization_reference 列（记录 GS1 授权来源）；
- barcode_records.range_id 增加外键约束；
- 来源类型白名单收敛为 internal / gs1_authorized（移除 import）；
- v1 -> v2 表重建迁移：保留全部数据，旧 import 来源映射为 internal。
"""

SCHEMA_VERSION = 2

VALID_RECORD_STATUSES = ("generated", "allocated", "used", "invalidated")
VALID_RANGE_STATUSES = ("active", "exhausted", "disabled")
VALID_SOURCE_TYPES = ("internal", "gs1_authorized")
VALID_BARCODE_TYPES = ("UPC-A", "EAN-13", "EAN-8")
VALID_ORIENTATIONS = ("portrait", "landscape")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS barcode_records (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode_value           TEXT NOT NULL UNIQUE
                            CHECK (length(barcode_value) IN (8, 12, 13)
                                   AND barcode_value NOT GLOB '*[^0-9]*'),
    barcode_type            TEXT NOT NULL
                            CHECK (barcode_type IN ('UPC-A', 'EAN-13', 'EAN-8')),
    source_type             TEXT NOT NULL DEFAULT 'internal'
                            CHECK (source_type IN ('internal', 'gs1_authorized')),
    authorization_reference TEXT,
    allocation_status       TEXT NOT NULL DEFAULT 'generated'
                            CHECK (allocation_status IN
                                   ('generated', 'allocated', 'used', 'invalidated')),
    product_name            TEXT,
    notes                   TEXT,
    range_id                INTEGER REFERENCES numbering_ranges(id),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    invalidated_at          TEXT,
    CHECK ((source_type = 'internal') OR
           (source_type = 'gs1_authorized' AND authorization_reference IS NOT NULL
            AND length(trim(authorization_reference)) > 0))
);
CREATE INDEX IF NOT EXISTS idx_barcode_records_type    ON barcode_records(barcode_type);
CREATE INDEX IF NOT EXISTS idx_barcode_records_status  ON barcode_records(allocation_status);
CREATE INDEX IF NOT EXISTS idx_barcode_records_created ON barcode_records(created_at);

CREATE TABLE IF NOT EXISTS numbering_ranges (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    range_name              TEXT NOT NULL CHECK (length(trim(range_name)) > 0),
    barcode_type            TEXT NOT NULL
                            CHECK (barcode_type IN ('UPC-A', 'EAN-13', 'EAN-8')),
    source_type             TEXT NOT NULL DEFAULT 'internal'
                            CHECK (source_type IN ('internal', 'gs1_authorized')),
    prefix                  TEXT NOT NULL DEFAULT ''
                            CHECK (prefix = '' OR prefix NOT GLOB '*[^0-9]*'),
    start_number            INTEGER NOT NULL CHECK (start_number >= 0),
    end_number              INTEGER NOT NULL CHECK (end_number >= start_number),
    next_number             INTEGER NOT NULL
                            CHECK (next_number >= start_number
                                   AND next_number <= end_number),
    authorization_reference TEXT,
    range_status            TEXT NOT NULL DEFAULT 'active'
                            CHECK (range_status IN ('active', 'exhausted', 'disabled')),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    CHECK ((source_type = 'internal') OR
           (source_type = 'gs1_authorized' AND authorization_reference IS NOT NULL
            AND length(trim(authorization_reference)) > 0))
);
CREATE INDEX IF NOT EXISTS idx_numbering_ranges_status ON numbering_ranges(range_status);

CREATE TABLE IF NOT EXISTS print_templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name  TEXT NOT NULL UNIQUE CHECK (length(trim(template_name)) > 0),
    paper_width    REAL NOT NULL CHECK (paper_width > 0),
    paper_height   REAL NOT NULL CHECK (paper_height > 0),
    label_width    REAL NOT NULL CHECK (label_width > 0 AND label_width <= paper_width),
    label_height   REAL NOT NULL CHECK (label_height > 0 AND label_height <= paper_height),
    rows           INTEGER NOT NULL CHECK (rows >= 1),
    columns        INTEGER NOT NULL CHECK (columns >= 1),
    margins        TEXT NOT NULL DEFAULT '{"top":5,"right":5,"bottom":5,"left":5}',
    horizontal_gap REAL NOT NULL CHECK (horizontal_gap >= 0),
    vertical_gap   REAL NOT NULL CHECK (vertical_gap >= 0),
    orientation    TEXT NOT NULL DEFAULT 'portrait'
                   CHECK (orientation IN ('portrait', 'landscape')),
    barcode_height REAL NOT NULL CHECK (barcode_height > 0),
    show_text      INTEGER NOT NULL CHECK (show_text IN (0, 1)),
    copies         INTEGER NOT NULL CHECK (copies >= 1),
    offset_x       REAL NOT NULL DEFAULT 0,
    offset_y       REAL NOT NULL DEFAULT 0,
    builtin        INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type   TEXT NOT NULL CHECK (length(operation_type) > 0),
    barcode_value    TEXT,
    operation_time   TEXT NOT NULL,
    operation_result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_logs_value ON operation_logs(barcode_value);
CREATE INDEX IF NOT EXISTS idx_operation_logs_time  ON operation_logs(operation_time);
"""

# ---------------------------------------------------------------------------
# v1 历史 schema（仅供迁移测试构造旧库使用，产品代码不再使用）
# ---------------------------------------------------------------------------
LEGACY_SCHEMA_SQL_V1 = """
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


def migrate_1_to_2(conn, now=None):
    """v1 -> v2 表重建迁移。

    - 保留全部业务数据；
    - 旧 source_type='import' 映射为 'internal'（F-06：import 不是合法来源类型）；
    - barcode_records 新增 authorization_reference 列。v1 记录本身不存授权编号，
      对 gs1_authorized 记录尝试从其编号范围继承授权编号；无法继承的降级为
      'internal' 并写入审计日志（不伪造授权状态，也不丢失记录）；
    - gs1_authorized 编号范围若本身缺少授权编号（仅可能来自手工篡改的旧库），
      迁移整体失败并回滚，由使用者修正后重试；
    - 重建索引与外键。
    调用方负责在外层开启事务；失败时由事务回滚保证旧 schema 与数据完好。
    """
    if now is None:
        from .db import utc_now_iso
        now = utc_now_iso()

    conn.execute("""CREATE TABLE barcode_records_v2 (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode_value           TEXT NOT NULL UNIQUE
                                CHECK (length(barcode_value) IN (8, 12, 13)
                                       AND barcode_value NOT GLOB '*[^0-9]*'),
        barcode_type            TEXT NOT NULL
                                CHECK (barcode_type IN ('UPC-A', 'EAN-13', 'EAN-8')),
        source_type             TEXT NOT NULL DEFAULT 'internal'
                                CHECK (source_type IN ('internal', 'gs1_authorized')),
        authorization_reference TEXT,
        allocation_status       TEXT NOT NULL DEFAULT 'generated'
                                CHECK (allocation_status IN
                                       ('generated', 'allocated', 'used', 'invalidated')),
        product_name            TEXT,
        notes                   TEXT,
        range_id                INTEGER REFERENCES numbering_ranges(id),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        invalidated_at          TEXT,
        CHECK ((source_type = 'internal') OR
               (source_type = 'gs1_authorized' AND authorization_reference IS NOT NULL
                AND length(trim(authorization_reference)) > 0))
    )""")
    # 授权编号可继承条件：记录标记 gs1_authorized，且其范围也是 gs1_authorized
    # 并持有非空授权编号。
    conn.execute(
        """INSERT INTO barcode_records_v2
           (id, barcode_value, barcode_type, source_type, authorization_reference,
            allocation_status, product_name, notes, range_id,
            created_at, updated_at, invalidated_at)
           SELECT b.id, b.barcode_value, b.barcode_type,
                  CASE WHEN b.source_type = 'gs1_authorized'
                            AND r.id IS NOT NULL
                            AND r.source_type = 'gs1_authorized'
                            AND r.authorization_reference IS NOT NULL
                            AND length(trim(r.authorization_reference)) > 0
                       THEN 'gs1_authorized'
                       ELSE 'internal' END,
                  CASE WHEN b.source_type = 'gs1_authorized'
                            AND r.id IS NOT NULL
                            AND r.source_type = 'gs1_authorized'
                            AND r.authorization_reference IS NOT NULL
                            AND length(trim(r.authorization_reference)) > 0
                       THEN trim(r.authorization_reference)
                       ELSE NULL END,
                  b.allocation_status, b.product_name, b.notes, b.range_id,
                  b.created_at, b.updated_at, b.invalidated_at
           FROM barcode_records b
           LEFT JOIN numbering_ranges r ON r.id = b.range_id"""
    )
    # 记录被降级的 gs1_authorized 记录（授权来源无法从范围验证），稍后写入审计日志。
    # 必须在 DROP 旧表之前从旧 barcode_records 计算。
    conn.execute(
        """CREATE TEMP TABLE _migration_downgrades AS
           SELECT b.barcode_value AS barcode_value
           FROM barcode_records b
           LEFT JOIN numbering_ranges r ON r.id = b.range_id
           WHERE b.source_type = 'gs1_authorized'
             AND NOT (r.id IS NOT NULL AND r.source_type = 'gs1_authorized'
                      AND r.authorization_reference IS NOT NULL
                      AND length(trim(r.authorization_reference)) > 0)"""
    )
    conn.execute("DROP TABLE barcode_records")
    conn.execute("ALTER TABLE barcode_records_v2 RENAME TO barcode_records")

    conn.execute("""CREATE TABLE numbering_ranges_v2 (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        range_name              TEXT NOT NULL CHECK (length(trim(range_name)) > 0),
        barcode_type            TEXT NOT NULL
                                CHECK (barcode_type IN ('UPC-A', 'EAN-13', 'EAN-8')),
        source_type             TEXT NOT NULL DEFAULT 'internal'
                                CHECK (source_type IN ('internal', 'gs1_authorized')),
        prefix                  TEXT NOT NULL DEFAULT ''
                                CHECK (prefix = '' OR prefix NOT GLOB '*[^0-9]*'),
        start_number            INTEGER NOT NULL CHECK (start_number >= 0),
        end_number              INTEGER NOT NULL CHECK (end_number >= start_number),
        next_number             INTEGER NOT NULL
                                CHECK (next_number >= start_number
                                       AND next_number <= end_number),
        authorization_reference TEXT,
        range_status            TEXT NOT NULL DEFAULT 'active'
                                CHECK (range_status IN ('active', 'exhausted', 'disabled')),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK ((source_type = 'internal') OR
               (source_type = 'gs1_authorized' AND authorization_reference IS NOT NULL
                AND length(trim(authorization_reference)) > 0))
    )""")
    conn.execute(
        """INSERT INTO numbering_ranges_v2
           (id, range_name, barcode_type, source_type, prefix, start_number,
            end_number, next_number, authorization_reference, range_status,
            created_at, updated_at)
           SELECT id, range_name, barcode_type,
                  CASE WHEN source_type = 'gs1_authorized' THEN source_type
                       ELSE 'internal' END,
                  prefix, start_number, end_number, next_number,
                  authorization_reference, range_status, created_at, updated_at
           FROM numbering_ranges"""
    )
    conn.execute("DROP TABLE numbering_ranges")
    conn.execute("ALTER TABLE numbering_ranges_v2 RENAME TO numbering_ranges")

    conn.execute("""CREATE TABLE print_templates_v2 (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        template_name  TEXT NOT NULL UNIQUE CHECK (length(trim(template_name)) > 0),
        paper_width    REAL NOT NULL CHECK (paper_width > 0),
        paper_height   REAL NOT NULL CHECK (paper_height > 0),
        label_width    REAL NOT NULL CHECK (label_width > 0 AND label_width <= paper_width),
        label_height   REAL NOT NULL CHECK (label_height > 0 AND label_height <= paper_height),
        rows           INTEGER NOT NULL CHECK (rows >= 1),
        columns        INTEGER NOT NULL CHECK (columns >= 1),
        margins        TEXT NOT NULL DEFAULT '{"top":5,"right":5,"bottom":5,"left":5}',
        horizontal_gap REAL NOT NULL CHECK (horizontal_gap >= 0),
        vertical_gap   REAL NOT NULL CHECK (vertical_gap >= 0),
        orientation    TEXT NOT NULL DEFAULT 'portrait'
                       CHECK (orientation IN ('portrait', 'landscape')),
        barcode_height REAL NOT NULL CHECK (barcode_height > 0),
        show_text      INTEGER NOT NULL CHECK (show_text IN (0, 1)),
        copies         INTEGER NOT NULL CHECK (copies >= 1),
        offset_x       REAL NOT NULL DEFAULT 0,
        offset_y       REAL NOT NULL DEFAULT 0,
        builtin        INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )""")
    conn.execute(
        """INSERT INTO print_templates_v2
           (id, template_name, paper_width, paper_height, label_width, label_height,
            rows, columns, margins, horizontal_gap, vertical_gap, orientation,
            barcode_height, show_text, copies, offset_x, offset_y, builtin,
            created_at, updated_at)
           SELECT id, template_name, paper_width, paper_height, label_width, label_height,
            rows, columns, margins, horizontal_gap, vertical_gap, orientation,
            barcode_height, show_text, copies, offset_x, offset_y, builtin,
            created_at, updated_at
           FROM print_templates"""
    )
    conn.execute("DROP TABLE print_templates")
    conn.execute("ALTER TABLE print_templates_v2 RENAME TO print_templates")

    conn.execute("""CREATE TABLE operation_logs_v2 (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_type   TEXT NOT NULL CHECK (length(operation_type) > 0),
        barcode_value    TEXT,
        operation_time   TEXT NOT NULL,
        operation_result TEXT NOT NULL
    )""")
    conn.execute(
        """INSERT INTO operation_logs_v2 (id, operation_type, barcode_value,
                                        operation_time, operation_result)
           SELECT id, operation_type, barcode_value, operation_time, operation_result
           FROM operation_logs"""
    )
    conn.execute("DROP TABLE operation_logs")
    conn.execute("ALTER TABLE operation_logs_v2 RENAME TO operation_logs")

    # 写入来源降级审计日志（从临时表），并清理临时表
    conn.execute(
        """INSERT INTO operation_logs
               (operation_type, barcode_value, operation_time, operation_result)
           SELECT 'migrate_source_downgrade', barcode_value, ?,
                  'gs1_authorized -> internal：授权来源无法从编号范围验证'
           FROM _migration_downgrades""",
        (now,),
    )
    conn.execute("DROP TABLE _migration_downgrades")

    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_barcode_records_type    ON barcode_records(barcode_type)",
        "CREATE INDEX IF NOT EXISTS idx_barcode_records_status  ON barcode_records(allocation_status)",
        "CREATE INDEX IF NOT EXISTS idx_barcode_records_created ON barcode_records(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_numbering_ranges_status ON numbering_ranges(range_status)",
        "CREATE INDEX IF NOT EXISTS idx_operation_logs_value ON operation_logs(barcode_value)",
        "CREATE INDEX IF NOT EXISTS idx_operation_logs_time  ON operation_logs(operation_time)",
    ):
        conn.execute(idx)
