"""数据访问对象：全部使用参数化 SQL，禁止字符串拼接。"""

from .db import utc_now_iso


def _conn_of(db, conn):
    return conn if conn is not None else db.conn


class BarcodeRecordDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, *, barcode_value, barcode_type, source_type="internal",
               allocation_status="generated", product_name=None, notes=None,
               range_id=None, conn=None):
        now = utc_now_iso()
        cur = _conn_of(self.db, conn).execute(
            """INSERT INTO barcode_records
               (barcode_value, barcode_type, source_type, allocation_status,
                product_name, notes, range_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (barcode_value, barcode_type, source_type, allocation_status,
             product_name, notes, range_id, now, now),
        )
        return cur.lastrowid

    def exists(self, value, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT 1 FROM barcode_records WHERE barcode_value = ?", (value,)
        ).fetchone()
        return row is not None

    def get(self, value, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM barcode_records WHERE barcode_value = ?", (value,)
        ).fetchone()
        return dict(row) if row else None

    def update_status(self, value, status, *, conn=None):
        now = utc_now_iso()
        c = _conn_of(self.db, conn)
        if status == "invalidated":
            cur = c.execute(
                """UPDATE barcode_records
                   SET allocation_status = ?, updated_at = ?, invalidated_at = ?
                   WHERE barcode_value = ? AND allocation_status != 'invalidated'""",
                (status, now, now, value),
            )
        else:
            cur = c.execute(
                """UPDATE barcode_records
                   SET allocation_status = ?, updated_at = ?
                   WHERE barcode_value = ?""",
                (status, now, value),
            )
        return cur.rowcount == 1

    def update_details(self, value, *, product_name=None, notes=None, conn=None):
        """仅允许修改 generated 状态的商品名称/备注；已分配编码不可改内容。"""
        cur = _conn_of(self.db, conn).execute(
            """UPDATE barcode_records
               SET product_name = ?, notes = ?, updated_at = ?
               WHERE barcode_value = ? AND allocation_status = 'generated'""",
            (product_name, notes, utc_now_iso(), value),
        )
        return cur.rowcount == 1

    def query(self, *, value=None, product_name=None, barcode_type=None,
              status=None, created_from=None, created_to=None,
              limit=200, offset=0, conn=None):
        sql = "SELECT * FROM barcode_records WHERE 1 = 1"
        params = []
        if value:
            sql += " AND barcode_value LIKE ?"
            params.append(f"%{value}%")
        if product_name:
            sql += " AND product_name LIKE ?"
            params.append(f"%{product_name}%")
        if barcode_type:
            sql += " AND barcode_type = ?"
            params.append(barcode_type)
        if status:
            sql += " AND allocation_status = ?"
            params.append(status)
        if created_from:
            sql += " AND created_at >= ?"
            params.append(created_from)
        if created_to:
            sql += " AND created_at <= ?"
            params.append(created_to)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        rows = _conn_of(self.db, conn).execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_by_status(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT allocation_status, COUNT(*) AS n FROM barcode_records GROUP BY allocation_status"
        ).fetchall()
        return {r["allocation_status"]: r["n"] for r in rows}


class NumberingRangeDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, *, range_name, barcode_type, source_type, prefix,
               start_number, end_number, next_number,
               authorization_reference=None, range_status="active", conn=None):
        now = utc_now_iso()
        cur = _conn_of(self.db, conn).execute(
            """INSERT INTO numbering_ranges
               (range_name, barcode_type, source_type, prefix, start_number,
                end_number, next_number, authorization_reference, range_status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (range_name, barcode_type, source_type, prefix, start_number,
             end_number, next_number, authorization_reference, range_status,
             now, now),
        )
        return cur.lastrowid

    def get(self, range_id, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM numbering_ranges WHERE id = ?", (range_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT * FROM numbering_ranges ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_next_number(self, range_id, next_number, *, conn=None):
        _conn_of(self.db, conn).execute(
            "UPDATE numbering_ranges SET next_number = ?, updated_at = ? WHERE id = ?",
            (next_number, utc_now_iso(), range_id),
        )

    def set_status(self, range_id, range_status, *, conn=None):
        _conn_of(self.db, conn).execute(
            "UPDATE numbering_ranges SET range_status = ?, updated_at = ? WHERE id = ?",
            (range_status, utc_now_iso(), range_id),
        )


class PrintTemplateDAO:
    def __init__(self, db):
        self.db = db

    def list(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT * FROM print_templates ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, template_id, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM print_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else None


class OperationLogDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, operation_type, barcode_value, result, *, conn=None):
        _conn_of(self.db, conn).execute(
            """INSERT INTO operation_logs
               (operation_type, barcode_value, operation_time, operation_result)
               VALUES (?, ?, ?, ?)""",
            (operation_type, barcode_value, utc_now_iso(), result),
        )

    def list_for_value(self, value, *, limit=100, conn=None):
        rows = _conn_of(self.db, conn).execute(
            """SELECT * FROM operation_logs
               WHERE barcode_value = ?
               ORDER BY id DESC LIMIT ?""",
            (value, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
