# CustomSkinLoader 日志解析插件（paperbot_csl）

- 状态：已完成
- 开始：2026-10-16
- 相关文件：`plugins/paperbot_csl/paperbot_csl.py`
- 参考素材：`logs/CustomSkinLoaderlog/*.log`（本地测试用，不入库）

## 目标

群成员把 Minecraft 的 CustomSkinLoader 日志（`CustomSkinLoader.log` / `CustomSkinLoader (1).log` 等）
发到群里，Bot 自动下载、解析并回复一份摘要。

## 触发方式

双通道监听，均按 `filename_pattern`（默认 `CustomSkinLoader.*\.log$`）匹配文件名：

1. 群文件上传（`GroupUploadNoticeEvent`）—— 走 `get_group_file_url` 拿地址；
2. 聊天框直接发文件（消息 `file` 段）—— 主通道，段内自带腾讯 ftn 直链。

## 解析要点

- **版本 / 生成时间 / 站点顺序 / 主玩家**：从固定锚点行提取；生成时间取全文最后一条时间戳。
- **皮肤 / 披风来源**：CSL 里皮肤与披风各自独立，按 `loadlist` 顺序取「第一个成功提供该资源的站点」。
  判定靠「谁返回的内容命中最终 SkinUrl/CapeUrl 的 hash」，不能只看最终 URL 域名——
  `textures.minecraft.net` 是 Mojang 的材质 CDN，ElyBy / TLauncher 只是引用它。
- **Mojang 材质藏在 base64 里**，需解码 `properties[].value` 再取 `textures.SKIN/CAPE.url`。
- **14.6a 及更早**：无 logger 标记、不输出 loadlist（用 14.6a 默认加载列表兜底）、
  只记录第一步 UUID 请求（靠最终材质 URL 的 CDN 域名兜底来源）。
- **14.x 老格式无线程来源标记**：靠「请求 URL 域名 → loadlist root」回退归属。
- 同 hash 多站点命中时，用 `loadlist` 顺序做 tie-break。

## 输出内容

正常解析时的摘要包含：

- CSL 版本（含 build）、游戏版本、生成时间
- 主玩家（皮肤来源为 Mojang 时标注「命中正版」）
- 皮肤来源（附模型 default/slim）、披风来源
- 特殊提示：皮肤与披风来源不一致、1.7.10 + 14.6a 强制默认模型、CLFCSL 版本建议
- 已加载站点列表（超出 `max_listed_sites` 截断）
- 老日志提示（超过 `stale_days` 天）

回复行为：

| 场景 | 行为 |
|---|---|
| 拿不到下载地址 / 解析异常 | 静默（仅记日志） |
| 文件超 `max_size_mb` | 提示文件过大并给出重新生成指引 |
| 非 CSL 日志 | 「这不像是CustomSkinLoader的日志，或异常加载的日志，请等待人工处理」 |
| CSL 但无加载记录（`incomplete`） | 提示日志不完整，建议重新生成 |
| 正常解析 | 结构化摘要 |

## 1.7.10 特殊处理

- **14.6a 不支持纤细模型**：1.7.10 + CSL ≤ 14.6a 时，日志里 `Model: default` 不代表真实材质，
  会在皮肤来源后追加提示。
- **CLFCSL 兼容层**：检测 `CompatibilityLayerForCustomSkinLoader` 或
  `Successfully transformed method` 痕迹；1.7.10 下装了 CLFCSL 但 CSL 不是
  14.15-SNAPSHOT-348 时，建议换回该版本。

## 遗留 / 后续

- （后续增强）出图版本。
- 更多站点类型：出现未知 `type` 时解析器需补充分支。
