<div align="center">

<img src="docs/screenshots/icon.png" width="132" alt="全球通">

# 全球通 · QuanQiuTong

**只读 CS2 控制台日志的桌面翻译工具。**

实时监听 `-condebug` 产生的 `console.log`，把游戏内聊天的英语、俄语等外语翻译成简体中文，
并把你的中文回复翻译成英语、俄语、日语、韩语等目标语言。内置**本地翻译记忆库**，
重复的句子不再重复请求在线 API，从而降低费用。

不注入 · 不 Hook · 不读内存 · 不改游戏文件 · **无需关闭 Trusted Mode**

[![Release](https://img.shields.io/github/v/release/Goduzun/QuanQiuTong?style=flat-square&color=5667f3)](https://github.com/Goduzun/QuanQiuTong/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-4ec9a0?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-5aa9e6?style=flat-square)](README.zh-CN.md)
[![Python](https://img.shields.io/badge/python-3.11-e0b055?style=flat-square)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-389%20passed-4ec9a0?style=flat-square)](#测试)

[**English**](README.md) · [**简体中文**](README.zh-CN.md)

<img src="docs/screenshots/live-dark.png" width="880" alt="实时翻译">

</div>

---

## 功能一览

- **实时翻译**：监听 `-condebug` 输出，外语聊天 → 简体中文
  - 支持 `[ALL]` / `[T]` / `[CT]` 频道、死亡标记、**全角冒号**（`：`）
  - 语言识别含西里尔字母启发式；已是中文的消息直接显示，不送 API
- **中文回复辅助**：中文 → 英语/俄语/日语/韩语，三态严格区分（见下）
- **对局隔离**：每局独立 `match_id` + 代际计数，迟到译文被丢弃，不污染新对局
- **三层数据保留**：当前局消息 / 历史对局 / 长期记忆（缓存·术语·模板）分离
- **启动重置**：每次启动清空实时翻译与历史对局，**保留长期缓存与词汇表**
- **现代界面**：左侧导航 + 顶部状态栏 + 五个页面，深色/浅色主题

### 中文回复：翻译即复制

```
输入中文  →  Ctrl+Enter（或点「翻译并复制」）  →  译文自动进剪贴板  →  进游戏 Ctrl+V 粘貼
```

翻译成功后**自动复制**，不需要再按任何复制按钮。

**本工具不会自动发送、也不模拟按键** —— 状态提示会明确写着
「切回游戏按 Ctrl+V 粘贴即可发送（本程序不会自动发送）」，
绝不声称已经替你发送过。

### 六级查找（省钱）

```
1. 人工修正   ← 优先级最高，AI 结果永远无法覆盖
2. 长期记忆   ← 完全相同的句子
3. 术语库     ← 词边界匹配（rush B 不会命中 brush B）
4. 句式模板   ← 数字槽位（3 enemies left → 还剩 3 个敌人）
5. 共用缓存
6. 调用 API   ← 以上全未命中才走这一步
```

缓存键为 `(source_hash, target_language, direction, cache_version)`，同一句翻成英语与俄语
是**两笔独立缓存**。失败（`NetworkError` / `AuthError` / `EmptyResponseError`）**绝不写入缓存**。

### 消息排列

**设置 → 消息排列**可选：

- **最新在底部 + 自动滚动**（默认）—— 像聊天软件
- **最新在顶部** —— 新消息插在最上面，不滚动也不会漏看

自动滚动尊重你的阅读：你往上翻时**不会**被强行拉回，而是出现 **↓ 回到最新** 按钮。

<div align="center">

| 深色 | 浅色 |
|---|---|
| <img src="docs/screenshots/settings-dark.png" width="410"> | <img src="docs/screenshots/settings-light.png" width="410"> |
| <img src="docs/screenshots/history.png" width="410"> | <img src="docs/screenshots/glossary.png" width="410"> |

<sub>设置页与历史对局 / 词汇表</sub>

</div>

---

## ⚠️ 重要：VAC 安全边界（请先阅读）

请**自行评估风险后使用**。任何第三方工具都无法承诺「100% 安全」，本工具不对任何账号封禁负责。

### 本工具运行时的全部行为

只有三件事：

1. 以**只读**方式打开 CS2 通过 `-condebug` 启动参数写到磁盘的 `console.log` 文本文件；
2. 按字节**增量**读取新增内容，把识别出的聊天行交给你在设置里配置的在线翻译 API；
3. 把原文与译文显示在**本工具自己的窗口**里。

除此之外没有别的行为。CS2 的 `-condebug` 是 Valve 官方提供的启动参数，`console.log` 是游戏自己写出的普通文本日志；读取一个普通文本文件不涉及对游戏进程的任何操作，因此本工具在运行时**完全独立于游戏进程**。

### 能力边界

| 维度 | 本工具的态度 | 具体含义 |
| --- | --- | --- |
| 游戏进程 | 完全不接触 | 不注入、不附加、不读取或写入内存、不 Hook |
| 游戏文件 | 完全不改动 | 不改动任何 CS2 或 VAC 文件 |
| 网络行为 | 仅出站调用翻译 API | 不劫持、不代理、不修改游戏自身的网络流量 |
| 输入设备 | 不接管 | 不模拟键盘或鼠标；译文由你手动复制 |
| 聊天发送 | 无此能力 | 第一阶段不实现任何自动发送或回写游戏聊天的功能 |
| VAC | 不做任何针对处理 | 不存在任何以绕过 VAC 为目的的代码 |

> 如果你更谨慎，可以让工具只监听日志、把译文显示在第二块屏幕或手机上；仍不建议在
> 官方对战中使用任何翻译工具，请以你自己的判断为准。

---

## 功能一览

### 接收翻译（外语 → 简体中文）

- 增量监听日志：字节偏移 + inode 检测，处理日志清空、重建、游戏重启；
- 编码回退：UTF-8 → GBK → Latin-1，兼容不同系统区域设置；
- 独立聊天识别：区分 `[ALL]` / `[CT]` / `[T]` / `[TEAM]` / `[Spectator]`、死亡聊天、
  裸标签、玩家名含空格、消息含冒号等形态；引擎日志一律忽略；
- 自动语言识别：西里尔、假名、谚文、汉字占比、拉丁语等；中文消息原样保留。

### 回复翻译（中文 → 目标语言）

- 独立中文输入框，支持英语、俄语、日语、韩语等 19 种常见语言及模型支持的任意语言代码；
- 根据最近一条已识别消息**建议**目标语言（可手动修改，自动建议不会覆盖你的手动选择）；
- 同时显示中文原文与译文，一键复制译文；
- 翻译失败时保留输入草稿；
- 内置常用战术短语收藏与快捷插入。

### 翻译记忆库（本地自学习缓存）

- 本地 SQLite 持久化：程序重启后缓存仍然有效；
- 精确缓存：相同句子（保守标准化后）直接复用译文，不再请求 API；
- 人工纠错：可编辑译文、标记正确、恢复上一版本、删除错误记录；
- 游戏术语库：地图点位 / 阵营 / 武器 / 战术 / 经济局 / 保枪 / 转点 / 补枪等，可增删改查；
- 句式模板：已确认的固定句式（如 `They're pushing {site}!`）在变量合法时直接复用；
- 双向隔离：同一原文翻译到不同语言、不同方向分别缓存，绝不串用；
- 统计：命中率、节省请求数、API 调用次数等；预计费用仅在配置了价格与 token 数据时显示。

---

## 使用方法

### 方式一：直接运行 EXE（无需安装 Python）

1. 在 Steam 中为 CS2 添加启动参数：右键 CS2 → 属性 → 启动选项，输入：

   ```
   -condebug
   ```

2. 下载并解压 `QuanQiuTong` 发布包，双击 `QuanQiuTong.exe`。
3. 首次启动会弹出配置向导：
   - **日志路径**：点「自动检测」，或手动选择
     `...\Counter-Strike Global Offensive\game\csgo\console.log`；
   - **API Base URL / Key / 模型**：填 OpenAI 官方或任意兼容接口；
   - 点「测试日志读取」「测试 API 连接」确认配置。
4. 进入游戏聊天即可看到实时翻译。关闭窗口默认最小化到系统托盘继续监听。

> 命令行/诊断版：`QuanQiuTongCLI.exe` 用于无界面运行与日志诊断（见下文）。

### 方式二：从源码运行（开发者）

```powershell
# Windows / PowerShell
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1   # 建环境并装依赖
powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1     # 启动桌面版
powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1 -Cli # 命令行模式
```

或手动：

```bash
pip install -r requirements.txt
python app.py                 # 桌面界面
python app.py --cli           # 命令行模式
python app.py --diagnose 日志文件   # 本地日志诊断（只分析，不上传）
```

### 命令行用法

```bash
python app.py --help
python app.py --cli                          # 无界面实时翻译
python app.py --diagnose console.log          # 诊断日志能否识别到聊天
python app.py --reply "冲B点，跟我走" --to en  # 翻译一条中文回复
python app.py --version
python app.py --reset-config                  # 重置配置
```

---

## 翻译记忆库：缓存策略与准确性边界

### 查询优先级（从高到低）

1. **已确认的人工修正**（`translation_memory.confirmed=1`）；
2. **完全匹配的翻译缓存**（AI 译文，未确认）；
3. **已确认的整句术语/短语**（`translation_terms`）；
4. **已确认的句式模板**（`translation_templates`，变量严格校验）；
5. 仍未命中 → **调用在线 AI API**，成功后自动写入缓存。

### 冲突处理规则

- **已确认的人工修正不会被未确认的 AI 结果覆盖**；
- 未确认的 AI 译文不会因为「重复出现」就自动变成「已确认」；
- 修改译文后立即更新对应缓存行（记录修订历史），下一次查询即用新译文；
- API 失败或返回空内容时**不写入缓存**，失败不会永久阻止后续重试。

### 缓存键（精确匹配，保守标准化）

缓存键 = 缓存结构版本 + 翻译方向 + 目标语言 + 标准化原文。

标准化只做**保守**处理：

- 去首尾空白、统一换行（CRLF/CR → LF）、折叠连续空白、清除 CS2 插入的零宽字符；
- **不删除**否定词、数字、点位、武器名、方向词；
- 不把语义不同的句子合并为同一键；
- 发送者姓名等元数据不混入原文。

### 相似句 / 模板复用的安全边界

- 模板只有**已确认**才会被复用，且必须满足：变量名属于已知类型、变量值通过类型校验、
  变量数量与顺序一致、整句与模板字面部分完全匹配、目标语言与方向一致；
- **不做向量相似度匹配**——两个句子相似不等于译文可互换；
- 含数字、否定、方向、点位、武器、人称或时态变化的句子默认不套用未确认译文；
- 任何不满足条件的句子都回退到在线 API。

### 术语匹配的安全边界

- 术语只作为**翻译提示**注入 prompt，不做输出层面的全局字符串替换；
- 拉丁术语按词边界匹配（`rush B` 不会匹配到 `brush B`），
  含大写字母的术语按大小写敏感匹配（`HE` 手雷不会匹配到 `he`）；
- CJK 术语按子串匹配。

---

## 数据库位置与数据管理

| 数据 | 位置 |
| --- | --- |
| 配置文件 `config.json` | `%APPDATA%\QuanQiuTong\`（Windows）/ `~/.config/cs2-chat-translator/`（Linux） |
| API Key 加密存储 `secrets.json` | 同上（Windows 使用 DPAPI 加密，不明文保存） |
| 翻译记忆库 `memory.db` | 同上（SQLite，含缓存/术语/模板/历史/统计） |
| 运行日志 `logs\app.log` | 同上（滚动，脱敏后不记录 Key） |

### 纠错

- 在主窗口消息表上**右键**某条译文 → 「编辑译文并保存到记忆库…」；
- 或点工具栏「记忆库」→「缓存记录」页，**双击**某行编辑，右键可「标记为正确 / 恢复上一版本 / 删除」。

### 清空缓存

工具栏「记忆库」→ 底部「清空缓存」。清空前会二次确认；清空后所有翻译重新请求在线 API
（会产生费用），但术语、模板与累计统计会保留。

### 导出与备份

「记忆库」对话框底部支持导出 JSON / CSV、备份整个 `memory.db`（含 WAL 检查点）。

---

## 配置说明

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| CS2 日志路径 | `console.log` 绝对路径 | 空（首次引导） |
| API Base URL | OpenAI 或兼容接口 | `https://api.openai.com/v1` |
| API Key | 只存 DPAPI 加密，绝不写明文 | 空 |
| 模型 | 如 `gpt-4o-mini` | `gpt-4o-mini` |
| 启用本地翻译记忆 | 关闭后回到纯在线模式 | 开 |
| 记录翻译历史 | 关闭不影响缓存功能 | 开 |
| 每千 token 价格（可选） | 用于估算节省费用；不填则只显示节省请求数 | 0（不估算） |

环境变量（优先级高于配置文件）：`CS2_LOG_PATH`、`CS2_API_BASE_URL`、`CS2_MODEL`、
`CS2_TRANSLATOR_API_KEY` / `OPENAI_API_KEY`。`.env` 示例见 `.env.example`。

---

## 构建 Windows EXE

> ⚠️ **平台限制**：PyInstaller 不支持跨平台交叉编译。要得到 Windows `.exe`，
> 必须在 **Windows** 上执行构建脚本。本项目仓库中的 `.spec` 文件与脚本已就绪，
> 在 Linux/macOS 上执行只能得到对应平台的可执行文件（仅用于验证打包流程）。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1   # 一次性
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1   # 一键构建
```

产物（`dist\`）：

- `QuanQiuTong\QuanQiuTong.exe` —— GUI 版（onedir，推荐分发整个目录）；
- `QuanQiuTong-onefile\QuanQiuTong.exe` —— GUI 单文件版（启动稍慢）；
- `QuanQiuTongCLI\QuanQiuTongCLI.exe` —— 命令行/诊断版；
- `SHA256SUMS.txt` —— 各产物的 SHA-256 校验和。

打包特性：不请求管理员权限、内嵌图标与版本信息、默认关闭 UPX（降低杀软误报概率）。

---

## 测试

全部测试使用 **Mock 假客户端 / 本地 Mock HTTP 服务器**，不消耗真实 API 额度、不需要联网。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1        # Windows
QT_QPA_PLATFORM=offscreen pytest tests/ -q                       # 任意平台
```

### 翻译记忆库测试报告（真实执行结果）

在 Linux 沙箱环境（Python 3.11、PySide6 6.x、SQLite 3）实际运行：

```
242 passed in 13.91s
```

新增的翻译记忆库测试共 44 项，逐项验证需求中的场景：

| # | 场景 | 结果 |
| --- | --- | --- |
| 1 | 相同英文句子重复出现，只请求一次 API | ✅ 通过 |
| 2 | 相同俄文句子重复出现，只请求一次 API | ✅ 通过 |
| 3 | 同一原文翻译成不同目标语言，不错误复用 | ✅ 通过 |
| 4 | 人工修正后，下一次命中修正后的译文 | ✅ 通过 |
| 5 | 含否定词的句子不会错误命中肯定句缓存 | ✅ 通过 |
| 6 | 点位和数字不同的句子不会错误套用未确认译文 | ✅ 通过 |
| 7 | 模板变量缺失或不合法时安全回退 | ✅ 通过 |
| 8 | API 失败不会污染有效缓存 | ✅ 通过 |
| 9 | 程序重启后缓存仍存在（引擎级） | ✅ 通过 |
| 10 | 并发查询同一句子避免重复 API 请求（单飞） | ✅ 通过 |
| 11 | 缓存删除后确实重新请求 API | ✅ 通过 |
| 12 | 双向翻译缓存不会串用语言方向 | ✅ 通过 |
| 13 | 完整流水线三次同句：1 次 API + 2 次缓存命中，重启后零 API | ✅ 通过 |

---

## 已知问题与限制

1. **Windows EXE 未在本机构建**：当前开发环境为 Linux，PyInstaller 无法交叉编译出
   Windows 可执行文件；已提供 `scripts\build_exe.ps1` 与 `.spec`，请在 Windows 上执行。
2. 聊天识别采用**保守策略**：只识别已知的频道标签形态，非标准格式可能不识别；
   可通过设置中的「自定义聊天正则」「通用模式」扩展，或先用 `--diagnose` 诊断。
3. 拉丁语言（英/法/德/西等）统一按「英语」处理，自动识别不细分；AI 翻译本身可处理。
4. 翻译质量取决于所配置的模型；术语/模板只是优化手段，不能替代模型能力。
5. 本工具不实现任何自动发送功能，译文需手动复制到游戏内。

---

## 后续建议

- 若希望减少封禁风险，可在官方对战之外（社区服/自定义对局）使用，或仅在观战/复盘时用；
- 研究「自动发送」前，必须独立评估官方支持方式、游戏规则、VAC 风险与人工确认机制，
  本工具第一阶段明确不实现任何发送能力；
- 可考虑为高频语言（俄/英）维护更完整的术语与模板，以进一步降低 API 费用。

---

## 致谢与参考

本工具属于「只读 CS2 `console.log` 并翻译聊天」这一社区思路的一个独立实现。立项调研阶段查阅过以下公开项目，用于确认 CS2 日志中聊天行的文本形态，以及只读方案在 Windows 上的可行性：

- [MeckeDev/cs_translate](https://github.com/MeckeDev/cs_translate)
- [MeckeDev/cs2-chat-translator](https://github.com/MeckeDev/cs2-chat-translator)
- [rowanchanner/cs2-live-translator-](https://github.com/rowanchanner/cs2-live-translator-)

**本项目与上述项目没有代码或文档上的派生关系**：实现语言、程序结构、界面、翻译后端、本地记忆库均为独立设计与编写，未复制其源代码、注释或文档文字。与其重合的部分仅限于不受版权保护的**事实与通用方法**——即 Valve 官方 `-condebug` 机制本身，以及 `console.log` 中聊天行的文本排列方式。

需要说明的是，上述仓库目前均**未附带开源许可证**，其代码与文档的著作权归各自作者所有。本项目不主张、也不依赖这些项目授予的任何许可。

如果你正是上述项目的作者，且认为本项目的表述仍有不妥，请通过 Issue 联系，我会立即修改或删除相关表述。

---

## 许可

MIT License。本项目只读取 CS2 官方 `-condebug` 日志，与 Valve 无任何关联。
