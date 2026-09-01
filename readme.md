# eve-ui-bot — EVE Online 建筑信息采集工具

基于 [Sanderling](https://github.com/Arcitectus/Sanderling) 64 位内存读取的 UI 树遍历机器人：
自动遍历 EVE Online 客户端的星域 → 星座 → 星系 → 建筑（空堡类结构）信息窗口，
采集每条建筑的名称、类型、所属军团，生成按日期存放的星域建筑报告。

```
全局搜索星域 → 星域信息(相关星座 tab) → 星座信息(相关星系 tab) → 星系信息(建筑 tab) → 逐条建筑 showinfo
```

所有数据来自客户端自己的 UI 树（只读内存），鼠标/键盘输入按 UI 树坐标注入；
不修改客户端、不解析网络封包。

> ⚠️ **本项目仅供学习与技术交流使用。** 内存读取与 UI 自动化可能违反 EVE Online
> 服务条款，由此产生的账号处罚等一切后果由使用者自行承担；请勿将本项目或其
> 采集数据用于任何商业用途。

## 环境要求

- Windows x64，EVE Online 客户端（ExeFile.exe）已登录、窗口可见
- 客户端界面为**简体中文**（脚本依赖「建筑」「相关星系」「星域」等界面文案定位）
- Python ≥ 3.11（无第三方依赖，`pyproject.toml` / `uv.lock` 已就绪，也可直接 `python -m venv .venv`）
- `read-memory-64-bit.exe` 放在本目录（约 112 MB，获取方式见下节，不入库）
- 全局搜索（放大镜）窗口可以使用

## Sanderling 内存读取器

当前使用的 Sanderling 版本为 **v2025-10-24**（commit `594a2339a63d7e946872a77c0d5772acdf75bd98`，发布于 2025-10-24），本地 `read-memory-64-bit.exe`（116 MB，2025-10-24 构建）即出自该版。

- 发布页：<https://github.com/Arcitectus/Sanderling/releases/tag/v2025-10-24>
- 直接下载（与本项目所用完全一致的包）：
  <https://github.com/Arcitectus/Sanderling/releases/download/v2025-10-24/read-memory-64-bit-self-contained-single-file-exe-594a2339a63d7e946872a77c0d5772acdf75bd98-win-x64.zip>

下载后把压缩包里的 `read-memory-64-bit.exe` 解压到本目录即可。也可以从
[Sanderling 源码](https://github.com/Arcitectus/Sanderling) 的
`implement/read-memory-64-bit` 目录自行构建（`build.bat`，依赖 .NET SDK）。

## 快速开始

```bash
# 批量采集全部星域
python run_all_regions.py --dry-run     # 预览执行计划（已完成/待执行/缺项回溯来源）
python run_all_regions.py               # 启动，交互询问「更新已有数据 / 全新生成」
python run_all_regions.py --update      # 增量更新，不询问
python run_all_regions.py --fresh       # 全量重扫，不询问

# 单个星域
python eve_ui_bot.py 伏尔戈 --update
python eve_ui_bot.py 伏尔戈 --fresh

# 工具
python eve_ui_bot.py --cleanup          # 关闭残留窗口、清空搜索框，恢复初始状态
python eve_ui_bot.py --test             # 离线提取器自测（需要 UI 快照夹具，见「测试夹具」一节）
python repair_reports.py                # 清理曾被“串窗”污染的星系建筑列表并标记待重扫
```

批量脚本可随时 Ctrl+C / 客户端掉线 / 服务器维护后直接重跑：已完成（今日目录中
`complete=true` 且星座数>0）的星域自动跳过，半成品星域以合并模式补全。

## 数据存储与更新模式

报告写入按日期的目录：`output/<YYYY-MM-DD>/region-report-<星域名>.json`（逻辑见 `report_store.py`）。

启动时的载入规则：

- **找最新日期目录**作为数据源；某星域在最新目录里缺失（当天还没跑到）时，
  **最多往前回看 2 个日期目录**，取该星域最近一次生成的报告；
- **更新已有数据**：找到的历史报告**先复制进今日目录、再原地更新**。复制以星域为
  单位——处理到哪个星域才复制哪个，然后逐星系与当前 UI 核对：两边都有→沿用旧条目
  不开窗；UI 有、报告没有→开窗提取新增建筑；报告有、UI 走完后没有→删除。
  中途中断不会留下“复制了一堆但没更新”的文件；
- **全新生成**：清空今日目录后全部重扫（较旧的日期目录不参与）。中断后想续跑请改用
  更新模式，今日的半成品会自动作为基线补全。

报告 JSON 结构：

```jsonc
{
  "星域": "伏尔戈",
  "星座数": 8,
  "complete": true,               // 星域级：全部星座走完才为 true
  "星座": [
    {
      "名称": "木本呂",
      "星系数": 7,
      "complete": true,           // 星座级：星系列表滚动走完才为 true
      "星系": [
        {
          "名称": "新加达里",
          "安全等级": "1.0",
          "建筑数": 4,
          "complete": true,       // 星系级：建筑列表滚动走完才为 true
          "建筑": [
            { "名称": "新加达里 - 办事处", "类型": "堡垒 - 空堡", "所属军团": "…" }
          ]
        }
      ]
    }
  ]
}
```

三级 `complete` 标志用于断点自愈：中断留下的半成品（false）在下次合并模式中
该层级会被完整重走一遍。

## 文件说明

| 文件 | 用途 |
|---|---|
| `eve_ui_bot.py` | 主机器人：UI 树解析、窗口导航、建筑提取、报告合并 |
| `run_all_regions.py` | 批量调度 68 个星域，模式选择、断点跳过、失败重试清单 |
| `report_store.py` | 报告存储：日期目录、旧文件迁移、基线回溯（最多 2 个目录）、模式询问 |
| `repair_reports.py` | 一次性修复：清空被串窗污染的星系建筑列表 |
| `click.ps1` / `scroll.ps1` / `drag.ps1` / `sendkeys.ps1` | 前台鼠标点击 / 滚轮 / 拖动 / 按键注入 |
| `param_echo.ps1` | 参数回显，调试 PowerShell 传参用 |
| `scan_links.py` / `extract-location-panel.py` / `test_scroll.py` | 开发期调试脚本（showinfo 链接解析 / 信息面板提取 / 滚动分页验证） |
| `read-memory-64-bit.exe` | Sanderling 内存读取器（外部二进制，获取方式见上文） |

## 测试夹具（离线自测）

`python eve_ui_bot.py --test` 不需要运行中的客户端，但要读取两份 UI 树快照作为
测试夹具。夹具是从实机客户端抓的（`snapshot-forge-*.json`），包含其他玩家的
界面文本，默认被 `.gitignore` 排除，需要自行抓取：

| 夹具文件 | 抓取时需打开的窗口 | 自测断言 |
|---|---|---|
| `snapshot-forge-region-mubuRuo-constellation-newcaldari-system.json` | 长征星域信息（相关星座 tab）、木本呂星座信息（相关星系 tab）、新加达里星系信息（建筑 tab） | 木本呂列出 7 个星系；新加达里有 4 条建筑 |
| `snapshot-forge-newcaldari-with-citadel-showinfo.json` | 在新加达里的建筑列表中再点开一个空堡的 showinfo 窗口 | 建筑类型解析为 “堡垒 - 空堡” |

实机抓取步骤：

1. 登录客户端，按上表把对应窗口打开（用全局搜索逐级打开 showinfo 即可），
   并关闭其它无关窗口，避免干扰窗口定位；
2. 查询 EVE 进程 PID：`tasklist /FI "IMAGENAME eq ExeFile.exe"`；
3. 抓取 UI 树（首次为全内存扫描，可能需要几分钟）：
   ```bat
   read-memory-64-bit.exe read-memory-eve-online --pid=<PID> --output-file=snapshot-forge-region-mubuRuo-constellation-newcaldari-system.json
   ```
4. 再点开一个空堡的 showinfo，同样命令抓取第二份
   （`--output-file=snapshot-forge-newcaldari-with-citadel-showinfo.json`）；
5. 若实际列表数量与断言不符（例如星系建筑数后来变了），同步修改
   `eve_ui_bot.py` 中 `self_test()` 里的断言数字。

另一种方式：带 `--save-snapshots` 跑一次采集，机器人会在每个关键窗口状态下
自动把 UI 树快照写入 `runs/` 目录，从中挑出对应状态的快照改名即可。

## 免责声明

- 本项目仅供**学习、研究与技术交流**使用，请自觉遵守游戏服务条款及所在地区法律法规，
  不得用于任何商业用途或破坏游戏环境的行为；
- 本项目按“原样”提供，**不附带任何明示或默示的保证**。使用本项目产生的任何后果
  （包括但不限于账号封禁、数据丢失）由使用者自行承担；
- 本项目与 CCP Games 及 EVE Online 官方没有任何关联；项目名称中提及的游戏仅用于
  说明兼容性；
- 采集的报告数据涉及其他玩家在游戏内的公开信息，分享数据集时请自行评估并承担相应责任。

## 开源协议

本项目以 [MIT License](LICENSE) 协议开源。

所依赖的 [Sanderling](https://github.com/Arcitectus/Sanderling) 内存读取器采用
Apache-2.0 协议发布；本项目未复制其源码，仅以独立进程方式调用其可执行文件。

## 隐私说明

采集结果包含其他玩家军团名称、玩家命名建筑等游戏内公开信息，属于个人扫描数据集；
`output/`、日志与原始 UI 快照默认被 `.gitignore` 排除，是否共享请自行决定。
`eve_ui_bot.py --test` 所需的两份 UI 快照夹具同属此类，不入库，
抓取方法见「测试夹具（离线自测）」一节。
