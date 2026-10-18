"""打印模板服务：校验 + CRUD + 预置模板恢复。"""

from ..database.dao import PrintTemplateDAO
from .templates import LabelTemplate, preset_templates


class TemplateError(Exception):
    """模板业务错误。"""


class TemplateService:
    def __init__(self, db):
        self.db = db
        self.dao = PrintTemplateDAO(db)

    def list_templates(self):
        return [LabelTemplate.from_dict(d) for d in self.dao.list()]

    def get_template(self, template_id):
        d = self.dao.get(template_id)
        return LabelTemplate.from_dict(d) if d else None

    def create_template(self, template):
        """创建自定义模板。errors 非空时拒绝并保持数据库不变。"""
        errors, _warnings = template.validate()
        if errors:
            raise TemplateError("；".join(errors))
        if self.dao.get_by_name(template.template_name):
            raise TemplateError(f"模板名称已存在: {template.template_name}")
        with self.db.transaction(immediate=True) as conn:
            return self.dao.insert(template, conn=conn)

    def update_template(self, template_id, template):
        errors, _warnings = template.validate()
        if errors:
            raise TemplateError("；".join(errors))
        existing = self.dao.get(template_id)
        if not existing:
            raise TemplateError(f"模板 {template_id} 不存在")
        if existing["builtin"]:
            raise TemplateError("预置模板不可修改，请先复制为自定义模板")
        with self.db.transaction(immediate=True) as conn:
            ok = self.dao.update(template_id, template, conn=conn)
        if not ok:
            raise TemplateError(f"模板 {template_id} 更新失败")
        return template_id

    def delete_template(self, template_id):
        existing = self.dao.get(template_id)
        if not existing:
            raise TemplateError(f"模板 {template_id} 不存在")
        if existing["builtin"]:
            raise TemplateError("预置模板不可删除")
        with self.db.transaction(immediate=True) as conn:
            ok = self.dao.delete(template_id, conn=conn)
        if not ok:
            raise TemplateError(f"模板 {template_id} 删除失败")
        return True

    def copy_template(self, template_id, new_name):
        if not new_name or not new_name.strip():
            raise TemplateError("新模板名称不能为空")
        with self.db.transaction(immediate=True) as conn:
            return self.dao.copy(template_id, new_name.strip(), conn=conn)

    def restore_presets(self):
        """补建缺失的预置模板（升级/数据迁移后使用）。"""
        created = 0
        with self.db.transaction(immediate=True) as conn:
            for t in preset_templates():
                if not self.dao.get_by_name(t.template_name, conn=conn):
                    self.dao.insert(t, conn=conn)
                    created += 1
        return created
