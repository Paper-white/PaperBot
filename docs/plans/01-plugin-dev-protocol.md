# 插件开发协议（对标 AstrBot 六项差距）

- 状态：已完成
- 开始：2026-09-30
- 相关文件：`src/core/{feature,hooks,version,reload,logger}.py`、`scripts/new_plugin.py`、`docs/plugin-dev.md`
- 相关文档：[`../plugin-dev.md`](../plugin-dev.md)（插件开发指南）、[`../plugin-api.md`](../plugin-api.md)（API 参考）

## 目标

让「写一个 PaperBot 插件」的体验接近成熟框架（AstrBot），同时**保留本项目更强的地方**：

- 命令权限可配置 + 即时生效（AstrBot 是代码里硬编码）
- 三级门控（成员 / 群 / 全局，就近优先）
- 消息输出守卫（AstrBot 没有这一层）
- 插件是「模块级 `Feature` 实例 + 装饰器」，可跨文件拆分

## 差距与落地

| 优先级 | 差距 | AstrBot 做法 | 本项目实现 | 位置 |
|---|---|---|---|---|
| P0 | 插件级配置项 | `_conf_schema.json` → WebUI 自动表单 | `Feature.config_schema` → WebUI 自动渲染 + 值存文件（见 03 号计划） | `pluginconf.py` |
| P1 | 生命周期钩子 | `initialize()` / `terminate()` | `on_load` / `on_unload` / `on_enable` / `on_disable`（三签名兼容） | `hooks.py` |
| P1 | 元信息 | `metadata.yaml`（author / repo / astrbot_version） | `Feature(..., author=, repo=, requires=)` + 加载期版本校验 | `version.py` |
| P2 | 插件热重载 | WebUI 可重载 | `POST /api/plugins/{name}/reload` + 前缀清理 + 失败回滚 | `reload.py` |
| P2 | 脚手架 | 官方插件模板 | `pixi run new-plugin <名字>` 一键生成骨架 | `scripts/new_plugin.py` |
| P3 | 统一 logger | `from astrbot.api import logger` | `PrefixedLogger` / `get_logger()`（自动带 `[插件名] ` 前缀） | `logger.py` |

## 关键决策

- **`requires` 语法手滑不拒绝插件**：`satisfies()` 逐条 try/except，**写错的条件被忽略**而不是拒绝加载
  （否则作者把 `>=1.0` 写成 `abc` 就整个插件加载不了，太狠）
- **命令前缀必须手动清**：NoneBot 的 `TrieRule.prefix` 是**类级全局单例**，`Matcher.destroy()` 不清它 →
  重载 / 拒绝加载后残留 `%cmd`。统一走 `feature.drop_command_prefixes()` 手动 `pop`
- **`_` 开头不能当配置 key**：留给框架元信息（`_plugin` / `_updated_by` / `_updated_at`）
- 自定义 `Permission` 子类**必须调 `super().__init__(...)`**（带 `__slots__`，不调就没有 `checkers` 属性 → 插件 import 直接失败）

## 落地清单

- [x] P0 插件级配置项（WebUI 自动表单，改完即时生效）
- [x] P1 生命周期钩子（0 参 / `feature` / `(feature, context)` 三种签名）
- [x] P1 元信息 + `requires` 校验（不满足 → 拒绝加载，其它插件照常）
- [x] P2 热重载（受保护插件 409；重载前 `on_unload`、重载后 `on_load`，配置保留）
- [x] P2 脚手架生成器（不覆盖已存在文件，除非 `--force`）
- [x] P3 统一 logger
- [x] `docs/plugin-dev.md` 插件开发指南文档（API 细节另见 `plugin-api.md`）
- [x] 真机复测：配置读写（含中文 UTF-8）/ 热重载 200 / 受保护 409

## 遗留 / 后续

- 早期自检脚本（`plugindev_check.py` 96 项等）放在 `%TEMP%\opencode\`，**已被系统清理**；
  改动这块时建议临时重写一个同样的自检跑一遍（长期入口只有 `_tests/config_check.py` 与 `_tests/llm_check.py`）
- `docs/plugin-dev.md` / `docs/plugin-api.md` 与代码同步维护：**改框架对外接口时顺手更新它**
