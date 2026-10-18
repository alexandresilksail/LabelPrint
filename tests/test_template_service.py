"""打印模板服务测试：CRUD、校验、预置保护（阶段 2）。"""

import tempfile
import unittest

from client.database.db import Database
from client.printing.template_service import TemplateError, TemplateService
from client.printing.templates import LabelTemplate


class TemplateServiceTestCase(unittest.TestCase):
    def make_service(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, TemplateService(db)


class TestTemplateCRUD(TemplateServiceTestCase):
    def test_presets_loaded_from_db(self):
        _, svc = self.make_service()
        templates = svc.list_templates()
        self.assertEqual(len(templates), 4)
        self.assertTrue(all(t.builtin for t in templates))

    def test_create_and_get(self):
        _, svc = self.make_service()
        t = LabelTemplate(template_name="我的模板", label_width=50, label_height=30,
                          rows=8, columns=4)
        tid = svc.create_template(t)
        got = svc.get_template(tid)
        self.assertEqual(got.template_name, "我的模板")
        self.assertEqual(got.label_width, 50)

    def test_create_invalid_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(TemplateError):
            svc.create_template(LabelTemplate(template_name="坏模板", rows=0))

    def test_create_duplicate_name_rejected(self):
        _, svc = self.make_service()
        svc.create_template(LabelTemplate(template_name="重名"))
        with self.assertRaises(TemplateError):
            svc.create_template(LabelTemplate(template_name="重名"))

    def test_update_ok(self):
        _, svc = self.make_service()
        tid = svc.create_template(LabelTemplate(template_name="可改", rows=5))
        svc.update_template(tid, LabelTemplate(template_name="可改", rows=6))
        self.assertEqual(svc.get_template(tid).rows, 6)

    def test_builtin_update_and_delete_protected(self):
        _, svc = self.make_service()
        builtin = [t for t in svc.list_templates() if t.builtin][0]
        bid = svc.dao.get_by_name(builtin.template_name)["id"]
        with self.assertRaises(TemplateError):
            svc.update_template(bid, LabelTemplate(template_name="篡改"))
        with self.assertRaises(TemplateError):
            svc.delete_template(bid)

    def test_copy_builtin(self):
        _, svc = self.make_service()
        builtin = [t for t in svc.list_templates() if t.builtin][0]
        bid = svc.dao.get_by_name(builtin.template_name)["id"]
        new_id = svc.copy_template(bid, "30x20 副本")
        copied = svc.get_template(new_id)
        self.assertEqual(copied.template_name, "30x20 副本")
        self.assertFalse(copied.builtin)
        # 副本可修改
        svc.update_template(new_id, LabelTemplate(template_name="30x20 副本",
                                                  rows=copied.rows + 1))

    def test_delete_custom(self):
        _, svc = self.make_service()
        tid = svc.create_template(LabelTemplate(template_name="临时"))
        self.assertTrue(svc.delete_template(tid))
        self.assertIsNone(svc.get_template(tid))

    def test_restore_presets_idempotent(self):
        _, svc = self.make_service()
        self.assertEqual(svc.restore_presets(), 0)  # 已存在
        tid = svc.create_template(LabelTemplate(template_name="占位"))
        svc.delete_template(tid)
        self.assertEqual(svc.restore_presets(), 0)


if __name__ == "__main__":
    unittest.main()
