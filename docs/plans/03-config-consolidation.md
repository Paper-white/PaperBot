# 配置集中化（`.config/`）

- 状态：已完成
- 开始：2026-10-13
- 相关文件：`src/config.py`、`src/core/pluginconf.py`、`webui/envfile.py`、`bot.py`、`_tests/config_check.py`
- 相关文档：[`../plugin-api.md`](../plugin-api.md) §5 插件级配置项

## 目标

原来配置是**三处散落**的：根目录 `.env`（框架）+ 若干零散文件 + 插件的配置存在 **SQLite 表**里。
要求统一：

1. 所有配置收进根目录 **`.config/`**，并整体 gitignore（含密钥，不进版本库）
2. **插件配置一个插件一个目录**：`.config/<插件名>/`，**JSON 格式**
3. 框架自身配置放 **`.config/main/`**
4. 插件读自己的配置**从这个目录读**，支持手改

## 最终布局

```
.config/                      ← 整个目录已 gitignore
├── main/.env                 ← 框架自身配置（原根目录 .env）
└── <插件名>/
    ├── config.json           ← 插件配置项（框架读写）
    └── ...                   ← 插件自己的文件（feature.config_dir）
```

## 方案与决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 插件配置存哪 | **JSON 文件**（废弃 `plugin_config` 表） | 用户可手改、可单目录备份；「一个插件一个目录」天然隔离 |
| 文件内容 | **只写显式改过的键** + `_` 开头的元信息 | 没配过的键实时回落 schema 默认值 → 文件永远干净；全空时**文件自动删除** |
| 原子性 | 先写同目录临时文件再 `os.replace` | 避免写一半崩掉把配置弄坏 |
| 坏文件怎么办 | 当「没配过」处理，且**不覆盖** | 用户手改错了还能自己救回来 |
| 框架配置格式 | 沿用 **`.env`（key=value）** | 只要「插件配置用 JSON」，框架那份没必要换格式；pydantic-settings / NoneBot 原生吃 env |
| 老的根目录 `.env` | **作为低优先级兜底**继续读 | `nonebot.init(_env_file=".config/main/.env")` 会自动拼成 `(".env", ".config/main/.env")`，后读覆盖先读 → 老环境零破坏 |
| 旧表数据 | 启动时搬进 JSON，表**改名归档**为 `plugin_config_legacy` | 不删数据，万一要回滚还在 |

## 落地清单

- [x] `pluginconf.py` 重写为文件读写（`plugin_dir` / `config_file` / `read_file` / `write_config` / `clear_config` / `clear_plugin_files` / `migrate_legacy_rows`）
- [x] `models.py` 删掉 `PluginConfig`；`bootstrap._migrate_legacy_plugin_configs()` 一次性迁移
- [x] `src/config.py`：`config_dir()` / `main_config_dir()` / `MAIN_ENV_PATH` + `env_file` 指向新位置
- [x] `bot.py`：`nonebot.init(_env_file=MAIN_ENV_PATH)`
- [x] `webui/envfile.py`：指向 `.config/main/.env` + `migrate_legacy_env()` + `read_path()` 兜底
- [x] `Feature.config_dir` property（插件放自己文件的地方，自动创建）
- [x] WebUI 插件配置接口加 `path` / `exists` / `updated_by` / `updated_at`
- [x] `.gitignore` 加 `.config/`
- [x] 自检 `_tests/config_check.py`（`pixi run check-config`，92 项）
- [x] 真机验证：PUT 中文配置 → 磁盘 JSON（无 BOM）+ 即时生效 + 删键回默认 + 全清删文件

## 踩坑记录

- ⚠️ **别用 `Path(".env")` 硬编码**：所有路径从 `src/config.py` 取，否则自检脚本没法把根目录重定向到临时目录
  （自检里要 monkeypatch `pluginconf.config_dir` 与 `envfile._ENV_PATH`）
- ⚠️ 写文件的**父目录要自己建**：`migrate_legacy_env()` 曾依赖 `main_config_dir()` 算路径，
  结果自检重定向后目录没建 → `FileNotFoundError`；改成 `_ENV_PATH.parent.mkdir(...)` 后自洽
- ⚠️ `.env.example` **留在根目录**：它是公开模板，放进 gitignore 的目录里就不会被提交了
- ℹ️ `plugin_dir()` 校验插件名（字母/数字/`_`/`-`/`.`），`..`、`a/b`、`.hidden` 一律拒绝，防目录穿越
- ℹ️ 手改 `config.json` 后**不需要重启**：下次读配置（重启 / 热重载 / WebUI 保存）就会生效

## 遗留 / 后续

- 若以后要把「框架配置」也 JSON 化，需同时改 `pydantic-settings` 与 NoneBot 两处读取路径（目前刻意不做）
