#!/usr/bin/env python
"""通过 GitHub REST API 推送本地提交到远端仓库。

适用场景：所在网络可访问 api.github.com 但无法直连 github.com（git 协议被阻断）。
仅用标准库，无需额外依赖。

用法：
    set GITHUB_TOKEN=ghp_xxx
    python scripts\\push_via_api.py [owner/repo] [branch]

令牌权限：需要 contents: write（fine-grained）或 repo（classic）。
脚本会读取 `git ls-files` 的受管文件（自动排除 .gitignore 内的大体积产物），
按当前 HEAD 的文件内容构造 blob/tree/commit，并更新远端分支引用。

注意：这不是 git 协议传输，不携带历史提交信息（多提交会被压成单个提交）。
如需保留完整历史，请在可直连 github.com 的环境执行 `git push`，
或使用仓库根目录生成的 `LabelPrint-main.bundle`。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def _request(method: str, path: str, token: str, payload: dict | None = None):
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "CodeLabelStudio-push")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return exc.code, {"error": detail}


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                         text=True, check=True)
    return out.stdout


def collect_files(root: Path) -> list[tuple[str, bytes, str]]:
    """读取 HEAD 中所有受管文件的内容与索引内记录的真实文件模式。

    不能用 os.access(path, os.X_OK) 判断可执行位：Windows 上它几乎恒为真，
    会把全部文件推成 100755，导致下游 clone 后所有文件都带可执行位。
    """
    listing = _git("ls-files", "-s", "-z", cwd=root).split("\0")
    files: list[tuple[str, bytes, str]] = []
    for entry in listing:
        if not entry:
            continue
        # 格式：<mode> <sha> <stage>\t<path>
        meta, _, rel = entry.partition("\t")
        if not rel:
            continue
        mode = meta.split(" ", 1)[0] if meta else "100644"
        if mode not in ("100644", "100755", "120000"):
            mode = "100644"      # 子模块等特殊类型按普通文件处理
        path = root / rel
        if not path.is_file():
            continue
        files.append((rel, path.read_bytes(), mode))
    return files


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("错误：未设置 GITHUB_TOKEN 环境变量", file=sys.stderr)
        return 2

    repo = sys.argv[1] if len(sys.argv) > 1 else "alexandresilksail/LabelPrint"
    branch = sys.argv[2] if len(sys.argv) > 2 else "main"
    root = Path(__file__).resolve().parents[1]

    print(f"目标仓库 {repo} / 分支 {branch}")

    status, info = _request("GET", f"/repos/{repo}", token)
    if status != 200:
        print(f"无法访问仓库（{status}）：{info.get('error', '')}", file=sys.stderr)
        return 1
    print(f"仓库就绪：{info['full_name']}（私有={info['private']}）")

    head_msg = _git("log", "-1", "--pretty=%s", cwd=root).strip()
    head_sha = _git("rev-parse", "HEAD", cwd=root).strip()

    files = collect_files(root)
    print(f"受管文件 {len(files)} 个，正在上传 blob…")

    tree_entries = []
    for i, (rel, content, mode) in enumerate(files, 1):
        status, blob = _request("POST", f"/repos/{repo}/git/blobs", token, {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        if status != 201:
            print(f"  失败 {rel}：{blob.get('error', '')}", file=sys.stderr)
            return 1
        tree_entries.append({
            "path": rel.replace("\\", "/"),
            "mode": mode,
            "type": "blob",
            "sha": blob["sha"],
        })
        if i % 25 == 0 or i == len(files):
            print(f"  {i}/{len(files)}")

    status, tree = _request("POST", f"/repos/{repo}/git/trees", token,
                            {"tree": tree_entries})
    if status != 201:
        print(f"创建 tree 失败：{tree.get('error', '')}", file=sys.stderr)
        return 1
    print(f"tree 已创建：{tree['sha'][:10]}")

    status, ref = _request("GET",
                           f"/repos/{repo}/git/ref/heads/{branch}", token)
    parents = []
    if status == 200:
        parents = [ref["object"]["sha"]]
        print(f"远端已有分支，将基于 {parents[0][:10]} 提交")
    else:
        print("远端为空仓库，创建首个提交")

    commit_payload = {
        "message": f"{head_msg}\n\nvia api (local {head_sha[:10]})",
        "tree": tree["sha"],
        "parents": parents,
    }
    status, commit = _request("POST", f"/repos/{repo}/git/commits", token,
                              commit_payload)
    if status != 201:
        print(f"创建 commit 失败：{commit.get('error', '')}", file=sys.stderr)
        return 1
    print(f"commit 已创建：{commit['sha'][:10]}")

    if parents:
        status, out = _request(
            "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token,
            {"sha": commit["sha"], "force": False})
    else:
        status, out = _request("POST", f"/repos/{repo}/git/refs", token,
                               {"ref": f"refs/heads/{branch}",
                                "sha": commit["sha"]})
    if status not in (200, 201):
        print(f"更新分支失败：{out.get('error', '')}", file=sys.stderr)
        return 1

    print(f"\n推送完成：https://github.com/{repo}/tree/{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
