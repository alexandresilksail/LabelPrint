"""第五轮：SHA-256 内容指纹绑定、提权前 preflight 一致性确认、竞态窗口收窄的
纯逻辑测试（Linux 全量可跑，不触碰任何 Windows 真机 API）。

诚信红线覆盖：
1. 指纹是内容身份的密码学强校验；size/mtime 只是常见变化检测线索，可被
   伪造（同大小同时间戳替换必须靠 SHA-256 捕获）。
2. preflight_launch：realpath 路径 / 大小 / mtime / SHA-256 全部一致才放行；
   任一不一致（含同大小同 mtime 替换、改 mtime、改大小、删除、status=None、
   缺指纹、符号链接改指）一律阻止，要求重新验证，无“继续冒险启动”旁路。
3. 符号链接：链接指向同一真实文件（已验证）→ ok；链接指向被替换文件 → block。
4. elevate_process 在 Linux 由最前的平台守卫抛 UNSUPPORTED_PLATFORM，守卫前
   不触碰文件、不产生副作用；preflight 是独立纯函数可测。
5. Windows 真机符号链接/交接点与 ShellExecuteW 行为待 Windows 执行验证，
   本文件不伪造任何真机调用。
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest

from client.printing.authenticode import (
    VerificationStatus,
    SignatureStatus,
    compute_file_sha256,
    describe_signature_status,
    file_identity_snapshot,
    preflight_launch,
    signature_stale,
)
from client.printing.driver_installer import elevate_process
from client.printing.errors import PrintError

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DRIVER_INSTALLER_PY = (
    _REPO_ROOT / "client" / "printing" / "driver_installer.py")


def _write(path: pathlib.Path, content: bytes) -> None:
    path.write_bytes(content)


def _status_from_file(path: pathlib.Path, *, verification=VerificationStatus.OK,
                      failure_reason=None) -> SignatureStatus:
    """从磁盘文件读取完整身份快照，构造绑定指纹的状态对象。"""
    size, mtime_ns, digest = file_identity_snapshot(str(path))
    return SignatureStatus(
        path=str(path),
        file_exists=True,
        verification=verification,
        failure_reason=failure_reason,
        file_size=size,
        file_mtime_ns=mtime_ns,
        sha256=digest,
    )


# ---------------------------------------------------------------------------
# SHA-256 指纹：幂等 / 异内容异指纹 / 64KB 分块与一次读一致 / 大文件
# ---------------------------------------------------------------------------
def test_fingerprint_idempotent_same_content(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"PE-BODY-v1" * 100)
    first = compute_file_sha256(str(f))
    second = compute_file_sha256(str(f))
    assert first == second
    assert isinstance(first, str) and len(first) == 64


def test_fingerprint_different_content_different(tmp_path):
    a = tmp_path / "a.exe"
    b = tmp_path / "b.exe"
    _write(a, b"content-one")
    _write(b, b"content-two")
    assert compute_file_sha256(str(a)) != compute_file_sha256(str(b))


def test_chunked_hash_matches_single_read(tmp_path):
    payload = bytes((i * 31 + 7) % 256 for i in range(1000))
    f = tmp_path / "chunk.exe"
    _write(f, payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert compute_file_sha256(str(f)) == expected


def test_large_file_over_64kb_stable(tmp_path):
    # 200KB，跨多个 64KB 分块；分块结果与一次读一致，且两次计算稳定。
    payload = bytes((i * 13 + 11) % 256 for i in range(200 * 1024))
    f = tmp_path / "large.exe"
    _write(f, payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert compute_file_sha256(str(f)) == expected
    assert compute_file_sha256(str(f)) == expected
    assert f.stat().st_size > 64 * 1024


def test_fingerprint_missing_file_returns_none(tmp_path):
    assert compute_file_sha256(str(tmp_path / "nope.exe")) is None
    assert compute_file_sha256("") is None


# ---------------------------------------------------------------------------
# preflight_launch：全一致 → ok
# ---------------------------------------------------------------------------
def test_preflight_ok_when_everything_matches(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"verified-body")
    status = _status_from_file(f)
    ok, reason = preflight_launch(str(f), status)
    assert ok is True
    assert reason is None


def test_preflight_ok_through_symlink_to_same_real_file(tmp_path):
    real = tmp_path / "real.exe"
    _write(real, b"verified-via-link")
    status = _status_from_file(real)
    link = tmp_path / "alias.exe"
    os.symlink(real, link)
    ok, reason = preflight_launch(str(link), status)
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# preflight_launch：阻止分支（核心：同大小同 mtime 替换靠指纹捕获）
# ---------------------------------------------------------------------------
def test_preflight_block_same_size_same_mtime_replaced(tmp_path):
    """核心用例：伪造同大小、同 mtime、内容不同的替换文件 → 指纹不符 → block。"""
    f = tmp_path / "setup.exe"
    _write(f, b"AAAAAAAAAA")  # 10 字节
    status = _status_from_file(f)
    orig_mtime = f.stat().st_mtime_ns

    # 等长替换内容，并把 mtime 伪造成与原文件完全相同
    _write(f, b"BBBBBBBBBB")
    os.utime(f, ns=(orig_mtime, orig_mtime))
    assert f.stat().st_size == status.file_size
    assert f.stat().st_mtime_ns == status.file_mtime_ns

    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason and "重新验证" in reason
    assert "SHA-256" in reason or "指纹" in reason


def test_preflight_block_mtime_changed(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"same-bytes")
    status = _status_from_file(f)
    future = f.stat().st_mtime_ns + 10 ** 9
    os.utime(f, ns=(future, future))
    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason and "修改时间" in reason


def test_preflight_block_size_changed(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"v1")
    status = _status_from_file(f)
    _write(f, b"v1-longer-body")
    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason and "大小" in reason


def test_preflight_block_deleted_file(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    status = _status_from_file(f)
    f.unlink()
    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason


def test_preflight_block_status_none():
    ok, reason = preflight_launch("C:\\x.exe", None)
    assert ok is False
    assert reason and "重新验证" in reason


def test_preflight_block_empty_path(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    status = _status_from_file(f)
    ok, reason = preflight_launch("   ", status)
    assert ok is False
    assert reason


def test_preflight_block_missing_fingerprint(tmp_path):
    """旧结果未携带 SHA-256 → 无法证明内容未被替换，保守阻止重验。"""
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    size, mtime_ns, _ = file_identity_snapshot(str(f))
    status = SignatureStatus(
        path=str(f), file_exists=True, verification=VerificationStatus.OK,
        file_size=size, file_mtime_ns=mtime_ns, sha256=None,
    )
    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason and "指纹" in reason


def test_preflight_block_incomplete_snapshot(tmp_path):
    """状态缺 size/mtime 快照 → 无法逐项比对，保守阻止重验。"""
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    _, _, digest = file_identity_snapshot(str(f))
    status = SignatureStatus(
        path=str(f), file_exists=True, verification=VerificationStatus.OK,
        file_size=None, file_mtime_ns=None, sha256=digest,
    )
    ok, reason = preflight_launch(str(f), status)
    assert ok is False
    assert reason and "快照不完整" in reason


def test_preflight_block_path_replaced_by_other_file(tmp_path):
    first = tmp_path / "first.exe"
    second = tmp_path / "second.exe"
    _write(first, b"first-content")
    status = _status_from_file(first)
    _write(second, b"second-content-different")
    ok, reason = preflight_launch(str(second), status)
    assert ok is False


# ---------------------------------------------------------------------------
# 符号链接：链接指向被替换文件 / 改指他处 → block
# ---------------------------------------------------------------------------
def test_preflight_block_symlink_to_replaced_target(tmp_path):
    real = tmp_path / "real.exe"
    _write(real, b"original-content")
    status = _status_from_file(real)
    link = tmp_path / "alias.exe"
    os.symlink(real, link)

    orig_mtime = real.stat().st_mtime_ns
    _write(real, b"replaced-content")
    os.utime(real, ns=(orig_mtime, orig_mtime))
    ok, reason = preflight_launch(str(link), status)
    assert ok is False
    assert reason


def test_preflight_block_symlink_repointed_to_other_file(tmp_path):
    a = tmp_path / "a.exe"
    b = tmp_path / "b.exe"
    _write(a, b"file-a")
    _write(b, b"file-b")
    status = _status_from_file(a)
    link = tmp_path / "link.exe"
    os.symlink(b, link)  # 链接改指未被验证的 b
    ok, reason = preflight_launch(str(link), status)
    assert ok is False


# ---------------------------------------------------------------------------
# signature_stale：携带指纹时，同大小同 mtime 替换也判 stale
# ---------------------------------------------------------------------------
def test_stale_true_on_same_size_mtime_replacement_with_fingerprint(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"AAAAAAAAAA")
    status = _status_from_file(f)
    orig_mtime = f.stat().st_mtime_ns
    _write(f, b"BBBBBBBBBB")
    os.utime(f, ns=(orig_mtime, orig_mtime))
    assert signature_stale(status, str(f)) is True


def test_stale_false_when_fingerprint_matches(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"unchanged")
    status = _status_from_file(f)
    assert signature_stale(status, str(f)) is False


# ---------------------------------------------------------------------------
# UI 文案：绑定指纹时展示“已绑定 SHA-256”
# ---------------------------------------------------------------------------
def test_describe_shows_bound_fingerprint_prefix(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"ok-body")
    status = _status_from_file(f)
    text = describe_signature_status(status)
    assert "已绑定 SHA-256" in text
    assert status.sha256[:12] in text


# ---------------------------------------------------------------------------
# elevate_process：Linux 守卫最前抛错，守卫前不触碰文件、无副作用
# ---------------------------------------------------------------------------
def test_elevate_process_linux_guard_raises_before_touching_file(tmp_path):
    # 不存在的路径：若守卫之后才触文件，会先报文件错误而非平台不支持。
    ghost = tmp_path / "ghost.exe"
    with pytest.raises(PrintError) as exc:
        elevate_process(str(ghost), ["/install"])
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
    assert not ghost.exists()  # 未创建/写入任何文件


def test_elevate_process_linux_guard_ignores_skip_flag(tmp_path):
    ghost = tmp_path / "ghost.exe"
    with pytest.raises(PrintError) as exc:
        elevate_process(str(ghost), skip_signature_check=True)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
    assert not ghost.exists()


# ---------------------------------------------------------------------------
# 源码顺序断言：Windows 分支 verify → preflight → realpath → ShellExecuteW
# ---------------------------------------------------------------------------
def test_windows_branch_order_verify_preflight_realpath_launch():
    text = _DRIVER_INSTALLER_PY.read_text(encoding="utf-8")
    i_verify = text.index("verify_authenticode(target)")
    i_preflight = text.index("preflight_launch(target, status)")
    i_realpath = text.index("os.path.realpath(target)")
    i_launch = text.index("shell32.ShellExecuteW")
    # 顺序：验证 → 启动前一致性确认 → 规范化路径 → 发起提权
    assert i_verify < i_preflight < i_realpath < i_launch
    # preflight 失败即 BLOCKED，无“继续冒险启动”旁路
    assert "继续冒险启动" in text
