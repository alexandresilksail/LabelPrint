"""RunLater 后台线程生命周期回归测试（离屏，不触碰业务数据）。

锁定两件事：
1. 后台 fn 执行完、主线程派发回调后，任务从活跃集合释放且线程已 finished；
2. 重复发起多个任务各自独立结束，不泄漏活跃线程。

不调用 shutdown_all（其会置全局退出标志，避免影响同进程其他用例）。
"""

import os
import time
import unittest

PYSIDE6_AVAILABLE = True
try:
    import PySide6  # noqa: F401
except ImportError:
    PYSIDE6_AVAILABLE = False


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 未安装")
class RunLaterLifecycleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        from client.ui import widgets
        widgets.SILENT_DIALOGS = True
        cls.app = QApplication.instance() or QApplication([])
        cls.widgets = widgets
        cls.QWidget = QWidget

    def _pump_until(self, predicate, timeout_s=5.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_single_task_finishes_and_releases(self):
        widgets = self.widgets
        done = []
        parent = self.QWidget()  # 弱引用可用的轻量父对象

        thread = widgets.RunLater(parent).run(
            lambda: 42, on_done=done.append)

        self.assertTrue(self._pump_until(lambda: done == [42]))
        self.assertTrue(thread.isFinished())
        with widgets._ACTIVE_LOCK:
            active = [e for e in widgets._ACTIVE_TASKS
                      if e.thread is thread]
        self.assertEqual(active, [])

    def test_concurrent_tasks_all_finish(self):
        widgets = self.widgets
        results = []
        parents = [self.QWidget() for _ in range(5)]

        for i, parent in enumerate(parents):
            widgets.RunLater(parent).run(
                lambda i=i: i, on_done=results.append)

        self.assertTrue(self._pump_until(lambda: len(results) == 5))
        self.assertEqual(sorted(results), [0, 1, 2, 3, 4])
        with widgets._ACTIVE_LOCK:
            self.assertEqual(len(widgets._ACTIVE_TASKS), 0)


if __name__ == "__main__":
    unittest.main()
