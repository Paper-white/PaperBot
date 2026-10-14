"""版本控制自检（`src/core/vcs.py` + `/api/version*` 路由）。

跑法：

    pixi run check-vcs

思路：在一个**临时目录里建一个假的 git 仓库**（本地 source + 远程 bare 两个库），
把 vcs 模块的 `BASE_DIR` 指过去，然后演练完整流程：

1. 仓库识别 / 当前 commit / 分支 / 干净状态；
2. `fetch` 拉到新提交、`ahead_behind` 落后数正确；
3. `update` 成功对齐（含：新文件出现、忽略目录不被触碰）；
4. 工作区脏 → 拒绝更新（code=dirty）；
5. 非法 / 不存在的 commit → 被拒（code=bad_commit / unknown_commit）；
6. 切到历史 commit（回滚）；
7. HTTP 层：`GET /api/version`、`POST /fetch`、`POST /update`（脏 409、非法 400）；
8. 守护服务检测（未配置 → state=none）。

全程**不碰**真实项目仓库与 `data/paperbot.db`。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((bool(ok), name, extra))
    mark = "OK  " if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if extra:
        line += f"  → {extra}"
    print(line)


def _git(cwd: Path, *args: str) -> str:
    """在指定目录跑 git（自检专用，非被测代码）。"""
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{out.stderr}")
    return out.stdout.strip()


def _setup_repos(tmp: Path) -> tuple[Path, Path]:
    """建 bare 远程 + 本地克隆，并造两个提交。返回 (local, remote)。"""
    remote = tmp / "remote.git"
    work = tmp / "source"
    local = tmp / "local"

    _git(tmp, "init", "--bare", "-b", "main", str(remote))
    _git(tmp, "init", "-b", "main", str(work))
    (work / "hello.txt").write_text("v1\n", encoding="utf-8")
    (work / ".gitignore").write_text("data/\nlogs/\n.config/\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "initial commit")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")

    # 克隆到 local（模拟生产环境）
    _git(tmp, "clone", str(remote), str(local))

    # 再造一个新提交并 push，模拟「本地开发机 push」
    (work / "feature.txt").write_text("new feature\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "add feature (from dev)")
    _git(work, "push", "origin", "main")
    return local, remote


def main() -> int:  # noqa: C901 - 自检脚本，长一点没关系
    tmp = Path(tempfile.mkdtemp(prefix="vcs_check_"))
    os.environ["DRIVER"] = "~none"

    local, remote = _setup_repos(tmp)

    import nonebot

    from src.config import settings

    settings.database_url = f"sqlite+aiosqlite:///{(tmp / 'check.db').as_posix()}"
    nonebot.init(driver="~none")

    from src.core import vcs

    # 关键：把 vcs 的 BASE_DIR 指到临时仓库（不动真实项目）
    vcs.BASE_DIR = local
    settings.vcs_remote = "origin"
    settings.vcs_branch = "main"
    settings.vcs_service_name = ""
    settings.vcs_log_limit = 30

    async def run() -> None:
        print("=" * 68)
        print("一、仓库识别与本地状态")
        print("=" * 68)
        check("is_repo 识别为 git 仓库", await vcs.is_repo())
        check("当前分支 = main", await vcs.current_branch() == "main")
        commit_before = await vcs.current_commit()
        check("能取到当前 commit", len(commit_before) >= 7, commit_before)
        check("工作区干净", not await vcs.is_dirty())
        check("remote_url 正确", "remote.git" in await vcs.remote_url())

        print("=" * 68)
        print("二、fetch 拉取远程新提交")
        print("=" * 68)
        result = await vcs.fetch()
        check("fetch 成功", result["ok"])
        check("落后 1 个提交", result["behind"] == 1, f"behind={result['behind']}")
        check("最新提交说明正确", "add feature" in (result["latest"] or {}).get("subject", ""))
        check("提交历史非空", len(result["commits"]) >= 2, f"共 {len(result['commits'])} 条")

        print("=" * 68)
        print("三、update 更新到最新（忽略目录不受影响）")
        print("=" * 68)
        # 造一个本地忽略目录，验证更新后它安然无恙
        (local / "data").mkdir(exist_ok=True)
        (local / "data" / "keep.db").write_text("precious\n", encoding="utf-8")

        upd = await vcs.update()
        check("更新成功且发生变化", upd["ok"] and upd["changed"], upd["message"])
        check("新文件 feature.txt 出现", (local / "feature.txt").is_file())
        check("被忽略的 data/keep.db 未被删除", (local / "data" / "keep.db").is_file())
        check("更新后无落后", (await vcs.ahead_behind())["behind"] == 0)

        print("=" * 68)
        print("四、工作区脏 → 拒绝更新")
        print("=" * 68)
        (local / "dirty.txt").write_text("手改\n", encoding="utf-8")
        try:
            await vcs.update()
            check("脏工作区应被拒绝", False)
        except vcs.VcsError as exc:
            check("脏工作区被拒绝（code=dirty）", exc.code == "dirty", exc.code)
        (local / "dirty.txt").unlink()

        print("=" * 68)
        print("五、非法 / 不存在的 commit → 被拒")
        print("=" * 68)
        try:
            await vcs.update("not-a-hash!!")
            check("非法 commit 应被拒绝", False)
        except vcs.VcsError as exc:
            check("非法 commit 被拒绝（code=bad_commit）", exc.code == "bad_commit", exc.code)
        try:
            await vcs.update("deadbeefdeadbeef")
            check("不存在的 commit 应被拒绝", False)
        except vcs.VcsError as exc:
            check(
                "不存在的 commit 被拒绝（unknown_commit）",
                exc.code == "unknown_commit",
                exc.code,
            )

        print("=" * 68)
        print("六、切到历史 commit（回滚）")
        print("=" * 68)
        hist = await vcs.commits(limit=5)
        oldest = hist[-1]["hash"]
        rb = await vcs.update(oldest)
        check("回滚到历史 commit", rb["ok"] and rb["changed"], rb["message"])
        check("回滚后 feature.txt 消失", not (local / "feature.txt").is_file())
        # 再切回最新，方便后续 HTTP 测试
        await vcs.fetch()
        await vcs.update()

        print("=" * 68)
        print("七、守护服务检测（未配置）")
        print("=" * 68)
        svc = await vcs.service_status()
        check("未配置守护服务 → state=none", svc["state"] == "none", svc.get("detail", ""))

        print("=" * 68)
        print("八、HTTP 接口")
        print("=" * 68)
        import httpx
        from fastapi import FastAPI

        from webui.routes import version as version_routes

        settings.vcs_enable = True
        app = FastAPI()
        app.include_router(version_routes.router)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/version")
            body = r.json()
            check("GET /version 200", r.status_code == 200, str(r.status_code))
            check("version.available=True", body.get("available") is True)

            r = await client.post("/version/fetch")
            check("POST /version/fetch 200", r.status_code == 200, str(r.status_code))
            check("fetch 返回 commits", len(r.json().get("commits", [])) >= 1)

            r = await client.post("/version/update", json={})
            check("POST /version/update 200", r.status_code == 200, str(r.status_code))

            # 脏工作区 → 409
            (local / "dirty2.txt").write_text("x\n", encoding="utf-8")
            r = await client.post("/version/update", json={})
            check("脏工作区更新 → 409", r.status_code == 409, str(r.status_code))
            (local / "dirty2.txt").unlink()

            # 非法 commit → 400
            r = await client.post("/version/update", json={"commit": "zzz"})
            check("非法 commit → 400", r.status_code == 400, str(r.status_code))

            # 关闭开关 → 400
            settings.vcs_enable = False
            r = await client.post("/version/fetch")
            check("关闭功能后 fetch → 400", r.status_code == 400, str(r.status_code))
            settings.vcs_enable = True

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 68)
    if passed == total:
        print(f"结果：{passed}/{total} 通过 ✅")
        return 0
    print(f"结果：{passed}/{total} 通过 ❌")
    for ok, name, extra in RESULTS:
        if not ok:
            print(f"  未通过：{name} {extra}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
