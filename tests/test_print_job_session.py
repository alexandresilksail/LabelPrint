"""PrintJobSession 任务会话管理测试（纯逻辑，无 Qt / pywin32 / 硬件）。

覆盖：
- 并发锁：同一打印机二次 begin_submit 抛 DEVICE_BUSY；不同打印机互不阻塞；
- 临时文件 keep_recent：保留最近 K 个、其余删除；cleanup_all 清空；
- cancel_active：无任务抛 JOB_NOT_FOUND；adapter.cancel 返回 False →
  SPOOL_FAILED；专用直连 NotImplementedError → TRANSPORT_NOT_IMPLEMENTED；
  取消成功才清 active；
- query_active：无任务抛 JOB_NOT_FOUND；专用直连 → TRANSPORT_NOT_IMPLEMENTED。
"""

import os
import tempfile

import pytest

from client.printing.errors import PrintError
from client.printing.job import JobState
from client.printing.print_session import PrintJobSession


class _FakeAdapter:
    """记录 cancel/query_job 调用的假适配器（不接触硬件）。"""

    def __init__(self, cancel_result=True, *, not_implemented=False,
                 query_state=None):
        self.cancel_result = cancel_result
        self.not_implemented = not_implemented
        self.query_state = query_state or JobState(job_id=1, status_text="排队中")
        self.calls = []

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        if self.not_implemented:
            raise NotImplementedError("raw 直连不支持 cancel")
        return self.cancel_result

    def query_job(self, job_id):
        self.calls.append(("query", job_id))
        if self.not_implemented:
            raise NotImplementedError("raw 直连不支持 query")
        return self.query_state


# ---------------------------------------------------------------------------
# 并发锁
# ---------------------------------------------------------------------------
class TestBeginSubmitLock:
    def test_same_printer_second_begin_raises_busy(self):
        s = PrintJobSession()
        s.begin_submit("Printer-A")
        with pytest.raises(PrintError) as ei:
            s.begin_submit("Printer-A")
        assert ei.value.code == PrintError.DEVICE_BUSY
        assert s.is_busy("Printer-A")

    def test_end_submit_releases_lock(self):
        s = PrintJobSession()
        s.begin_submit("Printer-A")
        s.end_submit("Printer-A")
        assert not s.is_busy("Printer-A")
        # 释放后可再次 begin
        s.begin_submit("Printer-A")
        assert s.is_busy("Printer-A")

    def test_different_printers_do_not_block_each_other(self):
        s = PrintJobSession()
        s.begin_submit("Printer-A")
        # 另一台打印机不被 A 的锁阻塞
        s.begin_submit("Printer-B")
        assert s.is_busy("Printer-A")
        assert s.is_busy("Printer-B")
        s.end_submit("Printer-A")
        assert not s.is_busy("Printer-A")
        assert s.is_busy("Printer-B")


# ---------------------------------------------------------------------------
# 临时文件生命周期
# ---------------------------------------------------------------------------
class TestTempLifecycle:
    def _mkfile(self, session):
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="sess_test_")
        os.close(fd)
        session.register_temp(path)
        return path

    def test_keep_recent_keeps_latest_deletes_older(self):
        s = PrintJobSession(keep_recent=2)
        paths = [self._mkfile(s) for _ in range(4)]
        for p in paths:
            assert os.path.isfile(p)
        s.cleanup()
        # 最近 2 个保留，前 2 个删除
        assert not os.path.exists(paths[0])
        assert not os.path.exists(paths[1])
        assert os.path.exists(paths[2])
        assert os.path.exists(paths[3])

    def test_cleanup_below_keep_threshold_keeps_all(self):
        s = PrintJobSession(keep_recent=2)
        paths = [self._mkfile(s) for _ in range(2)]
        s.cleanup()
        for p in paths:
            assert os.path.exists(p)

    def test_cleanup_all_clears_files_and_handles(self):
        s = PrintJobSession(keep_recent=2)
        paths = [self._mkfile(s) for _ in range(3)]
        s.set_active("Printer-A", _FakeAdapter(), 7, paths[0])
        s.cleanup_all()
        for p in paths:
            assert not os.path.exists(p)
        assert s.get_active("Printer-A") is None
        assert not s.is_busy("Printer-A")

    def test_cleanup_is_idempotent(self):
        s = PrintJobSession(keep_recent=1)
        self._mkfile(s)
        s.cleanup()
        s.cleanup()  # 不抛异常


# ---------------------------------------------------------------------------
# cancel_active
# ---------------------------------------------------------------------------
class TestCancelActive:
    def test_no_active_job_raises_not_found(self):
        s = PrintJobSession()
        with pytest.raises(PrintError) as ei:
            s.cancel_active("Printer-A")
        assert ei.value.code == PrintError.JOB_NOT_FOUND

    def test_adapter_cancel_false_raises_spool_failed(self):
        s = PrintJobSession()
        adapter = _FakeAdapter(cancel_result=False)
        s.set_active("Printer-A", adapter, 9)
        with pytest.raises(PrintError) as ei:
            s.cancel_active("Printer-A")
        assert ei.value.code == PrintError.SPOOL_FAILED
        # 失败不清 active
        assert s.get_active("Printer-A") is not None

    def test_adapter_not_implemented_raises_transport_not_implemented(self):
        s = PrintJobSession()
        adapter = _FakeAdapter(not_implemented=True)
        s.set_active("Printer-A", adapter, 9)
        with pytest.raises(PrintError) as ei:
            s.cancel_active("Printer-A")
        assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED

    def test_successful_cancel_clears_active(self):
        s = PrintJobSession()
        adapter = _FakeAdapter(cancel_result=True)
        s.set_active("Printer-A", adapter, 9)
        assert s.cancel_active("Printer-A") is True
        assert s.get_active("Printer-A") is None
        assert adapter.calls == [("cancel", 9)]

    def test_active_isolated_per_printer(self):
        s = PrintJobSession()
        s.set_active("Printer-A", _FakeAdapter(), 1)
        # B 无任务，取消 B 抛 JOB_NOT_FOUND，不影响 A
        with pytest.raises(PrintError) as ei:
            s.cancel_active("Printer-B")
        assert ei.value.code == PrintError.JOB_NOT_FOUND
        assert s.get_active("Printer-A") is not None


# ---------------------------------------------------------------------------
# query_active
# ---------------------------------------------------------------------------
class TestQueryActive:
    def test_no_active_job_raises_not_found(self):
        s = PrintJobSession()
        with pytest.raises(PrintError) as ei:
            s.query_active("Printer-A")
        assert ei.value.code == PrintError.JOB_NOT_FOUND

    def test_returns_job_state_for_active(self):
        s = PrintJobSession()
        state = JobState(job_id=3, status_text="打印中", is_finished=False)
        s.set_active("Printer-A", _FakeAdapter(query_state=state), 3)
        result = s.query_active("Printer-A")
        assert result is state
        assert result.status_text == "打印中"

    def test_not_implemented_raises_transport_not_implemented(self):
        s = PrintJobSession()
        s.set_active("Printer-A", _FakeAdapter(not_implemented=True), 3)
        with pytest.raises(PrintError) as ei:
            s.query_active("Printer-A")
        assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
