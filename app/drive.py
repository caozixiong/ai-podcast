"""Google Drive 上传与公开链接（rclone）。

上传 → 校验远端存在且大小一致 → 仅对本集 MP3 生成公开分享链接 → 提取文件 ID。
未配置 remote / 无授权时抛 DriveUnavailable（不假装成功）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


class DriveUnavailable(Exception):
    pass


def _rclone(args: list[str], timeout=300) -> str:
    rclone = shutil.which("rclone")
    if not rclone:
        raise DriveUnavailable("rclone 未安装。")
    try:
        out = subprocess.check_output([rclone] + args, stderr=subprocess.STDOUT, timeout=timeout)
        return out.decode(errors="replace")
    except FileNotFoundError:
        raise DriveUnavailable("rclone 未安装。")
    except subprocess.CalledProcessError as e:
        raise DriveUnavailable(f"rclone 失败：{(e.output or b'').decode(errors='replace')[:300]}")


def remote_exists(remote: str) -> bool:
    rclone = shutil.which("rclone")
    if not rclone:
        return False
    try:
        out = subprocess.check_output([rclone, "listremotes"], timeout=30).decode()
        return any(line.strip().rstrip(":") == remote for line in out.splitlines())
    except Exception:
        return False


def upload(local_mp3: str, remote: str, remote_path: str) -> dict:
    """返回 {remote_full, size_bytes, drive_id, share_url, preview_url}。"""
    if not remote_exists(remote):
        raise DriveUnavailable(
            f"rclone remote '{remote}' 不存在。请先 `rclone config` 创建 Google Drive remote 并完成授权。")
    base = os.path.basename(local_mp3)
    full = f"{remote}:{remote_path}/{base}"
    size_local = os.path.getsize(local_mp3)

    _rclone(["copyto", local_mp3, full])

    ls = json.loads(_rclone(["lsjson", f"{remote}:{remote_path}", "--files-only"]))
    hit = next((f for f in ls if f.get("Name") == base), None)
    if not hit:
        raise DriveUnavailable("上传后在远端找不到该文件。")
    if int(hit.get("Size", -1)) != size_local:
        raise DriveUnavailable(f"远端大小({hit.get('Size')}) 与本地({size_local}) 不一致。")

    link = _rclone(["link", full]).strip().splitlines()[-1].strip()
    m = re.search(r"file/d/([^/]+)/", link) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", link)
    if not m:
        raise DriveUnavailable(f"无法从 rclone link 输出解析 Drive 文件 ID：{link[:200]}")
    fid = m.group(1)
    return {
        "remote_full": full,
        "size_bytes": size_local,
        "drive_id": fid,
        "share_url": f"https://drive.google.com/file/d/{fid}/view",
        "preview_url": f"https://drive.google.com/file/d/{fid}/preview",
    }
