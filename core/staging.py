"""配对会话暂存：批量页上传的文件落 MEDIA_ROOT/tmp/batch/<session_id>/（ADR-0008）。

会话目录含 meta.json（{"user_id": ...}）与 "<file_id>__<安全化文件名>" 两类文件。
生命周期：提交成功立即 cleanup。每次 create_session 顺手清扫 mtime 超 TTL 的残留目录。
本模块只管文件落盘与归属校验，不做上传格式校验（views 层复用 vision.validate_upload）。
"""

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from django.conf import settings

SESSION_TTL_SECONDS = 30 * 60
MAX_SESSION_FILES = 30

_SAFE_NAME = re.compile(r"[^\w.\-\u4e00-\u9fff]+")


def _root():
    return Path(settings.MEDIA_ROOT) / "tmp" / "batch"


def _safe_name(name):
    cleaned = _SAFE_NAME.sub("_", Path(name or "").name).strip("._") or "file"
    if len(cleaned) > 80:
        # 超长截断时保留扩展名：丢了后缀会让识别按文件名错走图片路径
        # （腾讯云发票文件名超 80 字符，实测截掉 .pdf 后 PDF 被当图发给识别服务拒收）
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and 0 < len(suffix) <= 10 and suffix.isalnum():
            cleaned = (cleaned[:79 - len(suffix)].rstrip("._") or "file") + "." + suffix
        else:
            cleaned = cleaned[:80]
    return cleaned


def sweep():
    """清扫 mtime 超 TTL 的残留会话目录。"""
    root = _root()
    if not root.exists():
        return
    now = time.time()
    for entry in root.iterdir():
        try:
            if now - entry.stat().st_mtime > SESSION_TTL_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def create_session(user_id, uploads):
    """持久化一批上传文件，返回 (session_id, [{"id", "name"}, ...])。"""
    sweep()
    session_id = uuid.uuid4().hex
    session = _root() / session_id
    session.mkdir(parents=True)
    (session / "meta.json").write_text(json.dumps({"user_id": user_id}), encoding="utf-8")
    files = []
    for upload in uploads:
        file_id = uuid.uuid4().hex
        name = _safe_name(upload.name)
        upload.seek(0)
        (session / f"{file_id}__{name}").write_bytes(upload.read())
        files.append({"id": file_id, "name": name})
    return session_id, files


def session_dir(session_id, user_id):
    """返回归属校验通过的会话目录。会话不存在或不属于该用户抛 ValueError。"""
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id or ""):
        raise ValueError("会话无效")
    session = _root() / session_id
    meta = session / "meta.json"
    try:
        owner = json.loads(meta.read_text(encoding="utf-8")).get("user_id")
    except (OSError, ValueError):
        raise ValueError("会话已过期，请重新上传")
    if owner != user_id:
        raise ValueError("会话不属于当前用户")
    return session


def file_path(session, file_id):
    """返回会话内的暂存文件路径。不存在抛 ValueError。"""
    if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
        raise ValueError("文件无效")
    for entry in session.iterdir():
        if entry.name.startswith(file_id + "__"):
            return entry
    raise ValueError("文件不存在，可能已过期")


def file_label(path):
    """从暂存文件名还原原始文件名。"""
    return path.name.split("__", 1)[1] if "__" in path.name else path.name


def cleanup(session_id):
    shutil.rmtree(_root() / session_id, ignore_errors=True)
