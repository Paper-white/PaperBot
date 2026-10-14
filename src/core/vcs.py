"""框架版本控制（一键拉取云端版本）。

用途：**本地开发机 push → 服务器生产环境在 WebUI 点一下拉取最新代码**。

设计原则（务必记住）：

1. **只拉不推**：只做 `fetch` / `reset --hard`，绝不 `push`、绝不切换远程、绝不自动装依赖；
2. **绝不碰本地资产**：`.config/`（含密钥）`data/`（数据库）`logs/` 全部已 gitignore，
   `git reset --hard` **天然不会动**它们；本模块也**绝不执行 `git clean`**；
3. **生产环境不容忍手改**：工作区有未提交改动时**直接拒绝更新**（409），
   逼使用者「要改就回本地改好再 push」，避免生产代码悄悄偏离版本库；
4. **命令注入防护**：commit 入参用正则强校验，且所有 git 命令都走
   `asyncio.create_subprocess_exec`（列表参数、**不走 shell**）。

重启提供了两种模式（`VCS_RESTART_MODE`）：

- ``spawn``：自己起一个 detached「小助手」，等 3 秒（让旧进程释放端口）后拉起新进程，
  **开箱即用**；缺点是不是"崩溃自愈"，只有主动点重启才会重新拉起；
- ``exit``：只做优雅收尾后退出，交给 systemd / NSSM 等**守护进程**（`Restart=always`）拉起，
  这是长期常驻生产环境的推荐做法。

另外提供 :func:`service_status` 读取守护服务（systemd / Windows 服务）是否在运行，
供 WebUI 状态卡片展示。
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

import nonebot

from src.config import BASE_DIR, settings

logger = nonebot.logger

# git 命令默认超时（秒）：fetch 要联网，单独放宽
DEFAULT_TIMEOUT = 15.0
FETCH_TIMEOUT = 60.0

# commit 入参白名单：7~40 位十六进制（短 hash / 完整 hash 都接受）
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

# git log 用的字段分隔符（用不可见字符，避免与提交信息里的字符冲突）
_LOG_SEP = "\x1f"
_LOG_FMT = f"%H{_LOG_SEP}%h{_LOG_SEP}%an{_LOG_SEP}%aI{_LOG_SEP}%s"


class VcsError(RuntimeError):
    """版本控制操作失败（工作区脏 / 命令超时 / 非仓库等）。

    ``code`` 是给 WebUI 用的语义错误码，路由层据此映射 HTTP 状态码。
    """

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# 底层：执行 git 命令
# --------------------------------------------------------------------------- #
def _git_args(args: list[str]) -> list[str]:
    """组装 git 命令参数（顺带把代理透传进去，不污染仓库配置）。"""
    base = ["git", "-C", str(BASE_DIR)]
    proxy = (settings.vcs_proxy or "").strip()
    if proxy:
        # ⚠️ 只对本次命令生效，不写进 .git/config
        base += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    return base + args


async def _run(
    args: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = True,
) -> tuple[int, str, str]:
    """执行一条 git 命令，返回 (returncode, stdout, stderr)。"""
    cmd = _git_args(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # 禁止交互式要凭据
        )
    except FileNotFoundError as exc:  # git 没装
        raise VcsError("未找到 git 命令，请先安装 Git 并加入 PATH", code="no_git") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise VcsError(f"git 命令超时（>{timeout:.0f}s）：{' '.join(args)}", code="timeout") from exc

    stdout = (out or b"").decode("utf-8", "replace").strip()
    stderr = (err or b"").decode("utf-8", "replace").strip()
    if check and proc.returncode != 0:
        detail = stderr or stdout or f"退出码 {proc.returncode}"
        raise VcsError(f"git {' '.join(args)} 失败：{detail}", code="git_failed")
    return proc.returncode, stdout, stderr


# --------------------------------------------------------------------------- #
# 状态查询
# --------------------------------------------------------------------------- #
async def is_repo() -> bool:
    """当前项目目录是否是一个 git 仓库。"""
    if not (BASE_DIR / ".git").exists():
        # .git 可能是文件（worktree / submodule），用 git 自己确认一下更稳
        pass
    try:
        code, out, _ = await _run(["rev-parse", "--is-inside-work-tree"], check=False)
    except VcsError:
        return False
    return code == 0 and out.strip() == "true"


async def _git_version() -> str:
    code, out, _ = await _run(["--version"], check=False)
    return out if code == 0 else ""


async def current_commit(full: bool = False) -> str:
    """当前 HEAD 的 commit（默认短 hash）。"""
    args = ["rev-parse", "HEAD"] if full else ["rev-parse", "--short", "HEAD"]
    _, out, _ = await _run(args)
    return out


async def current_branch() -> str:
    """当前分支名（分离头指针时返回 commit）。"""
    _, out, _ = await _run(["rev-parse", "--abbrev-ref", "HEAD"])
    return out


async def is_dirty() -> bool:
    """工作区是否有未提交改动（含未跟踪文件）。"""
    _, out, _ = await _run(["status", "--porcelain"])
    return bool(out.strip())


async def dirty_files(limit: int = 50) -> list[str]:
    """列出未提交改动（供提示用）。"""
    _, out, _ = await _run(["status", "--porcelain"])
    files = [line.strip() for line in out.splitlines() if line.strip()]
    return files[:limit]


async def remote_url(remote: Optional[str] = None) -> str:
    """远程地址（取不到返回空串）。"""
    name = (remote or settings.vcs_remote or "origin").strip()
    code, out, _ = await _run(["remote", "get-url", name], check=False)
    return out if code == 0 else ""


async def _local_head() -> str:
    _, out, _ = await _run(["rev-parse", "HEAD"])
    return out


async def _remote_head(remote: str, branch: str) -> Optional[str]:
    """`<remote>/<branch>` 的 commit（不存在返回 None）。"""
    code, out, _ = await _run(
        ["rev-parse", "--verify", f"refs/remotes/{remote}/{branch}"], check=False
    )
    return out if code == 0 and out else None


async def ahead_behind() -> dict[str, int]:
    """本地相对 `<remote>/<branch>` 的领先 / 落后提交数。

    以「本地相对远程」为准：`behind` = 远程比本地多的提交（需要更新的量），
    `ahead` = 本地比远程多的提交（生产环境正常应为 0）。
    """
    remote = settings.vcs_remote or "origin"
    branch = settings.vcs_branch or "main"
    if await _remote_head(remote, branch) is None:
        return {"ahead": 0, "behind": 0, "unknown": True}
    code, out, _ = await _run(
        ["rev-list", "--left-right", "--count", f"HEAD...{remote}/{branch}"],
        check=False,
    )
    if code != 0 or not out:
        return {"ahead": 0, "behind": 0, "unknown": True}
    parts = out.split()
    if len(parts) != 2:
        return {"ahead": 0, "behind": 0, "unknown": True}
    ahead, behind = int(parts[0]), int(parts[1])
    return {"ahead": ahead, "behind": behind, "unknown": False}


async def status() -> dict[str, Any]:
    """汇总当前仓库状态（供 WebUI 状态卡片展示）。"""
    remote = settings.vcs_remote or "origin"
    branch = settings.vcs_branch or "main"
    if not await is_repo():
        return {
            "enabled": settings.vcs_enable,
            "available": False,
            "reason": "当前项目目录不是一个 git 仓库",
            "git_version": await _git_version(),
            "remote": remote,
            "branch": branch,
            "remote_url": "",
            "restart_mode": settings.vcs_restart_mode,
            "service": await service_status(),
        }

    repo_branch = await current_branch()
    url = await remote_url(remote)
    counts = await ahead_behind()
    return {
        "enabled": settings.vcs_enable,
        "available": True,
        "reason": "",
        "git_version": await _git_version(),
        "commit": await current_commit(),
        "commit_full": await current_commit(full=True),
        "branch": repo_branch,
        "target_branch": branch,
        "dirty": await is_dirty(),
        "dirty_files": await dirty_files(),
        "remote": remote,
        "remote_url": url,
        "ahead": counts["ahead"],
        "behind": counts["behind"],
        "compare_unknown": counts.get("unknown", False),
        "restart_mode": settings.vcs_restart_mode,
        "service": await service_status(),
    }


# --------------------------------------------------------------------------- #
# 提交历史
# --------------------------------------------------------------------------- #
def _parse_commits(raw: str) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    for line in raw.splitlines():
        parts = line.split(_LOG_SEP)
        if len(parts) < 5:
            continue
        full, short, author, date, subject = parts[:5]
        commits.append(
            {
                "hash": full,
                "short": short,
                "author": author,
                "date": date,
                "subject": subject,
            }
        )
    return commits


async def commits(ref: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """列出某个 ref 的提交历史（默认 `<remote>/<branch>`，新的在前）。"""
    remote = settings.vcs_remote or "origin"
    branch = settings.vcs_branch or "main"
    target = ref or f"{remote}/{branch}"
    count = int(limit or settings.vcs_log_limit or 30)
    code, out, _ = await _run(
        ["log", f"-n{count}", f"--pretty=format:{_LOG_FMT}", target],
        check=False,
    )
    if code != 0:
        return []
    return _parse_commits(out)


# --------------------------------------------------------------------------- #
# 拉取 / 更新
# --------------------------------------------------------------------------- #
async def fetch() -> dict[str, Any]:
    """拉取 `<remote>/<branch>`（不会改动工作区），返回更新情况与提交列表。"""
    if not await is_repo():
        raise VcsError("当前项目目录不是一个 git 仓库", code="no_repo")

    remote = settings.vcs_remote or "origin"
    branch = settings.vcs_branch or "main"

    # 首次部署可把远程地址写进 .env，这里按需补齐 / 更新 remote URL
    desired_url = (settings.vcs_remote_url or "").strip()
    if desired_url:
        existing = await remote_url(remote)
        if existing != desired_url:
            if existing:
                await _run(["remote", "set-url", remote, desired_url])
            else:
                await _run(["remote", "add", remote, desired_url])

    before = await _local_head()
    await _run(["fetch", "--prune", remote, branch], timeout=FETCH_TIMEOUT)
    remote_head = await _remote_head(remote, branch)
    if remote_head is None:
        raise VcsError(f"远程 {remote}/{branch} 不存在，请检查分支名", code="no_branch")

    counts = await ahead_behind()
    behind = counts["behind"]
    up_to_date = before == remote_head

    latest = await commits(limit=1)
    return {
        "ok": True,
        "up_to_date": up_to_date,
        "behind": behind,
        "ahead": counts["ahead"],
        "local_commit": before[:7],
        "remote_commit": remote_head[:7],
        "latest": latest[0] if latest else None,
        "commits": await commits(),
        "message": "已是最新版本" if up_to_date else f"发现 {behind} 个新提交",
    }


async def update(target: Optional[str] = None) -> dict[str, Any]:
    """把工作区对齐到某个 commit（默认 `<remote>/<branch>` 最新）。

    - **工作区有未提交改动时直接拒绝**（``code="dirty"``）；
    - ``target`` 为 commit hash 时做正则校验（防命令注入），并对齐到它；
    - 全程 `git reset --hard`，**绝不 `git clean`**，因此 `.config/` 等忽略目录安然无恙。
    """
    if not await is_repo():
        raise VcsError("当前项目目录不是一个 git 仓库", code="no_repo")

    remote = settings.vcs_remote or "origin"
    branch = settings.vcs_branch or "main"

    # 先做入参校验（非法 commit 优先报 400），再检查工作区是否干净
    if target:
        text = str(target).strip()
        if not _COMMIT_RE.match(text):
            raise VcsError(f"非法的 commit：{text!r}（应为 7~40 位十六进制）", code="bad_commit")
        # 先确认这个 commit 在本地存在
        code, _, _ = await _run(["cat-file", "-e", f"{text}^{{commit}}"], check=False)
        if code != 0:
            raise VcsError(f"提交 {text} 在本地不存在（可先「检查更新」拉取）", code="unknown_commit")
        reset_to = text
    else:
        reset_to = ""

    if await is_dirty():
        files = await dirty_files()
        preview = "、".join(files[:5])
        more = f" 等 {len(files)} 个文件" if len(files) > 5 else ""
        raise VcsError(
            f"工作区有未提交改动（{preview}{more}），已拒绝更新。"
            "请先在本地改好并 push，或手动处理这些改动",
            code="dirty",
        )

    before = await current_commit()

    if not reset_to:
        remote_head = await _remote_head(remote, branch)
        if remote_head is None:
            raise VcsError("尚未获取远程分支，请先点「检查更新」", code="no_fetch")
        reset_to = f"{remote}/{branch}"

    await _run(["reset", "--hard", reset_to])

    after = await current_commit()
    path_changed = await _changed_paths(before, after)

    return {
        "ok": True,
        "changed": before != after,
        "before": before,
        "after": after,
        "target": reset_to,
        "paths": path_changed,
        "deps_changed": any(
            p in ("pyproject.toml", "pixi.lock", "pixi.toml") for p in path_changed
        ),
        "message": (
            f"已更新 {before} → {after}" if before != after else "已是最新版本（未发生变化）"
        ),
    }


async def _changed_paths(old: str, new: str) -> list[str]:
    """两个 commit 之间变化的文件路径（用于提示依赖是否变更）。"""
    if old == new:
        return []
    code, out, _ = await _run(["diff", "--name-only", old, new], check=False)
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 守护服务状态
# --------------------------------------------------------------------------- #
async def service_status() -> dict[str, Any]:
    """检测守护服务（systemd / Windows 服务）是否在运行。

    服务名由 `VCS_SERVICE_NAME` 提供；留空则返回「未配置」。
    检测失败不抛错，返回 ``state="unknown"`` 让前端如实展示。
    """
    name = (settings.vcs_service_name or "").strip()
    if not name:
        return {"configured": False, "state": "none", "detail": "未配置守护服务"}

    if sys.platform.startswith("win"):
        return await _windows_service_status(name)
    return await _systemd_service_status(name)


async def _systemd_service_status(name: str) -> dict[str, Any]:
    async def _sysctl(verb: str) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", verb, name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return None
        return (out or b"").decode("utf-8", "replace").strip()

    active = await _sysctl("is-active")
    if active is None:
        return {"configured": True, "state": "unknown", "detail": "未找到 systemctl"}
    enabled = await _sysctl("is-enabled")
    # is-active 输出：active / inactive / failed / activating / unknown ...
    state = "running" if active == "active" else active
    return {
        "configured": True,
        "state": state,
        "active": active,
        "enabled": enabled,
        "detail": f"{name}：{active}" + (f"（开机自启：{enabled}）" if enabled else ""),
        "manager": "systemd",
    }


async def _windows_service_status(name: str) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "sc", "query", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return {"configured": True, "state": "unknown", "detail": "未找到 sc 命令"}

    text = (out or b"").decode("utf-8", "replace")
    match = re.search(r"STATE\s*:\s*\d+\s+(\w+)", text)
    if not match:
        # 服务不存在时 sc 会返回非零并提示「指定的服务未安装」
        return {
            "configured": True,
            "state": "not_found",
            "detail": f"未找到服务 {name}",
            "manager": "windows",
        }
    state_word = match.group(1).upper()
    running = state_word == "RUNNING"
    return {
        "configured": True,
        "state": "running" if running else state_word.lower(),
        "active": "active" if running else "inactive",
        "detail": f"{name}：{state_word}",
        "manager": "windows",
    }


# --------------------------------------------------------------------------- #
# 重启
# --------------------------------------------------------------------------- #
def _spawn_helper(delay: float = 3.0) -> int:
    """起一个 detached「小助手」，延迟若干秒后重新拉起 bot.py。

    ⚠️ 必须 detached（脱离当前进程组），否则主进程退出时会被一并带走。
    """
    entry = str(BASE_DIR / "bot.py")
    python = sys.executable
    cwd = str(BASE_DIR)

    # 用一段内联脚本：先 sleep，再用 Popen 起新进程（新进程再 detached，与助手解耦）
    code = (
        "import subprocess, sys, time;"
        f"time.sleep({delay!r});"
        "subprocess.Popen("
        f"[{python!r}, {entry!r}], cwd={cwd!r},"
        " close_fds=True,"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
        ")"
    )

    import subprocess  # 局部 import，避免影响模块加载

    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform.startswith("win"):
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：脱离控制台与进程组
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen([python, "-c", code], **popen_kwargs)
    return proc.pid


async def _graceful_shutdown() -> None:
    """尽力做一次优雅收尾（复用框架的关闭钩子）。"""
    try:
        from src.core.bootstrap import on_shutdown

        await on_shutdown()
    except Exception as exc:  # noqa: BLE001 - 收尾失败也要退出
        logger.opt(exception=exc).warning("重启前的优雅收尾失败，仍将继续退出")


async def request_restart() -> dict[str, Any]:
    """触发进程重启。

    - ``spawn`` 模式：先起 detached 小助手，再优雅收尾并退出（助手稍后拉起新进程）；
    - ``exit`` 模式：只优雅收尾并退出，交给守护进程（systemd/NSSM）拉起。

    注意：本函数**会安排进程退出**，WebUI 响应要先返回再退出（由路由层保证）。
    """
    mode = (settings.vcs_restart_mode or "spawn").strip().lower()
    helper_pid = 0
    if mode == "spawn":
        try:
            helper_pid = _spawn_helper()
        except Exception as exc:  # noqa: BLE001
            raise VcsError(f"无法启动重启助手：{exc}", code="spawn_failed") from exc

    logger.warning(f"收到重启请求（模式：{mode}），进程即将退出…")
    await _graceful_shutdown()

    # 硬退出：此时各资源已释放，用 os._exit 避免 uvicorn / asyncio 卡在清理上
    os._exit(0)  # noqa: SLF001 - 主动控制的立即退出
    return {"ok": True, "mode": mode, "helper_pid": helper_pid}  # pragma: no cover


__all__ = [
    "VcsError",
    "status",
    "commits",
    "fetch",
    "update",
    "service_status",
    "request_restart",
]
