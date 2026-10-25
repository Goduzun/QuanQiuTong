# Changelog · 更新日志

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

本项目的重要变更都记录在此。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

[**English**](#english) · [**简体中文**](#简体中文)

---

## English

### [1.2.6] — 2026-10-25

Fixes the **CS2 AppHangB1 / "not responding"** issue: CS2 stuttered periodically while this
app was running, and the app window itself could stop responding.

#### Fixed
- **CS2 stuttered while the app was running.** The engine pushed a status snapshot every
  0.5 s on its own thread, and each one triggered a full window refresh — so the window
  repainted ~1.9×/s even with CS2 in the foreground. Status pushes now drop to one per
  2 s while CS2 is foreground, and the window touches no UI at all in that state; a single
  catch-up refresh runs when you return. **Measured: 1.9 → 0 refreshes per second.**
- **The app itself could be flagged as not responding on exit.** `engine.stop()` blocked the
  UI thread for up to 22 s. The window is now hidden first, each step is capped at 1.5 s,
  and the callback pool is no longer waited on.
- **Duplicate instances.** A second launch now wakes the existing window and exits, instead
  of running another copy that doubles file polling and redraws.
- **Log reads hammered a missing or rebuilt file** every 250 ms. Now 250 ms → 500 ms → 1 s →
  2 s, reset on success.
- **One SQLite query per second** from the status bar (a likely contributor to the high
  handle count). Now cached for 5 s, and the subtitle is only rewritten when it changes.
- **The packaged build failed to start** because the slimming list stripped `Qt6Network`,
  which `QLocalServer` needs. It is kept now.
- Late `api_test_done` / `log_test_done` results arriving after the window closed raised
  `RuntimeError: Signal source has been deleted`.

#### Changed
- **Foreground awareness.** While `cs2.exe` is foreground the log poll interval goes
  0.25 s → 1.5 s, non-essential UI updates stop, and messages are queued and rendered in one
  pass when you come back.
- Startup API self-check now uses a **5 s timeout with no retries** instead of the 20 s
  translation timeout.
- The packaged build keeps the `qoffscreen` platform plugin, so it can be verified headlessly
  without popping windows onto the desktop.

#### Verified
435 automated tests pass. UI refresh rate measured at **0 per second** while CS2 is
foreground. Confirmed fixed in a real full-screen play session.

### [1.2.3] — 2026-10-22

#### Added
- **The reply box clears itself once your message actually goes out.**
  After a translation is copied and you paste it into the game, the app watches
  `console.log` for your own line; when it sees it, the input box is cleared so the
  next reply can be typed straight away.
  - Detected two ways: the sender matches the new **"My in-game name"** setting, or —
    with that left empty — the incoming text is compared against the translation that
    was just copied (whitespace, punctuation and case are normalised, partial matches
    allowed).
  - **Guarded against data loss:** the box is only cleared when an *un-sent
    translation is pending*. Typing a draft before translating, then saying something
    unrelated in game, leaves the draft untouched.

#### Changed
- Settings gained a **My in-game name** field under Language settings (optional).

### [1.2.2] — 2026-10-14

#### Changed
- **Reply flow simplified: translating now copies automatically.**
  The real workflow is *translate → it's on your clipboard → `Ctrl+V` in game*, so the
  separate "Copy translation" button and the "I've sent it" confirmation were both
  redundant. The button is now **Translate & Copy**, and a single status line reports
  state (ready / translating / copied / failed) instead of three widgets in a row.
  - The status line still **never claims a message was sent**, and spells out
    "this app does not send anything automatically".
- Settings section headings lost their `A.` / `B.` / `C.` prefixes.

#### Added
- **API check on startup** — the status bar no longer sits on "API not tested" until you
  visit Settings; it runs one check ~1.5 s after launch (only when a key is configured).
- **Update check on startup** — queries the public GitHub Releases API for the latest
  version number. It sends **no local data**, fails silently offline, and can be turned
  off entirely in Settings.

#### Fixed
- **Translation-memory page stutter** — same O(n²) trap as the glossary: with
  `ResizeToContents` columns, Qt recomputed column widths after *every* `setItem`
  (64 rows × 7 columns). Rebuilding while hidden brought page switching to **13.5 ms**.
  Search input is now debounced by 180 ms, and statistics use SQL aggregates
  (`term_stats()` plus a new `template_count()`) instead of fetching 100 000 rows.
- **Glossary and memory tables looked empty.** `resizeColumnsToContents()` also resizes
  `Stretch` columns to their content width, so the six glossary columns totalled only
  480 px inside a 1116 px table — leaving more than half the width blank. Now only the
  `ResizeToContents` columns are measured; both tables fill **100%** of their viewport.
- **Crash-on-close race.** `sqlite3.ProgrammingError: Cannot operate on a closed database`
  could appear when the status timer or a debounced search fired after the engine had
  already closed the database. `Database` now tracks a closed flag — reads return
  `None` / `[]` after close, while `transaction()` still raises, so a silently dropped
  write can't hide a real problem. Pages also expose `stop_timers()`, called during
  shutdown, and the update thread is joined.

### [1.2.1] — 2026-10-14

#### Fixed
- **Critical: removed OpenSSL DLLs broke all translation.**
  v1.2.0's slimming pass excluded `libssl-1_1.dll` / `libcrypto-1_1.dll` based on the
  incorrect assumption that "Python 3.11 uses OpenSSL 3.x". In reality this Python's
  `_ssl.pyd` imports them directly (OpenSSL 1.1.1u). The result: `_ssl` failed to load →
  `openai` could not be imported → translation stopped entirely, with the GUI showing only
  a single line about it.
  - Both DLLs removed from the exclusion lists; a prominent warning now sits next to the
    list so nobody repeats this.
  - Size cost: 32.52 MB → 34.01 MB (+1.49 MB). Still 37.5% smaller than the original 54.44 MB.

#### Added
- **`--diagnose` runtime self-check.** Reports whether `ssl` and `openai` actually load,
  and prints an actionable fix when they don't:
  ```
  ==== 运行环璄自检 ====
    ssl: OK  OpenSSL 1.1.1u  30 May 2023
    openai: OK  2.54.0
  ```
- **`TestPackagingSpec`** (5 tests) — locks down that no spec may exclude the OpenSSL DLLs,
  the required Qt modules, or the warning comment explaining why.

#### Notes
- Why the v1.2.0 verification missed this: the HTTPS check was run against the `.venv`
  interpreter (which has full OpenSSL) rather than the packaged EXE. Verification now
  targets the build artefact itself.

### [1.2.0] — 2026-10-14

#### Added
- **Message ordering setting** — choose *newest at bottom + auto-scroll* (default) or
  *newest at top*, so new messages are never missed when you aren't scrolling.
- **WCAG AA compliance across the whole palette** — 15 colour pairs measured and corrected.
  8 pairs were failing, e.g. the primary button's white text was 3.59:1 against the
  accent colour (needs 4.5:1); light-theme `success` was 3.29:1.
- **Design token unification** — type scale opened up to 11/12/14/16/20/24 (13 and 14 were
  only 1 px apart), spacing aligned to a 4 pt grid, radii moved to 6/10/14.
- **Packaging slimming: 54.44 MB → 32.52 MB (−40.3%).** Every exclusion justified by PE
  import-table analysis (see README → Building).

#### Changed
- `PageHeader` no longer renders a title — the shell's top bar owns the page title.
  Previously both used `objectName="pageTitle"`, so every page showed its title twice,
  both at 24 px.
- The Settings page's duplicate `section_title("设置")` removed.
- `QLabel` is now transparent by default. Qt paints a rectangle for every label once
  `QWidget` has a background, and the global `QWidget` colour (`bg_surface`) is *darker*
  than the page background (`bg_root`) — which put a visible dark box behind every label.

#### Fixed
- Auto-scroll could silently stop working: the "is the user at the bottom?" check was
  computed from `value`/`maximum` on every message, but the scroll range hasn't updated
  yet when a message is inserted, so "still at the bottom" was misread as "scrolled away".
  Now tracked with an explicit flag.
- Glossary performance: rebuilding the table with `ResizeToContents` columns was O(n²)
  (Qt recomputes column widths after *every* `setItem`). 153 rows took 1300–3200 ms and
  got slower on each visit. Rebuilding while hidden + one `resizeColumnsToContents()`
  call → **8–14 ms**. Search input also debounced by 180 ms.
- Statistics switched from fetching 100 000 rows into Python to a SQL aggregate
  (`term_stats()`, 0.025 ms).
- Subtitle in the page header no longer wraps to two lines when sharing a row with buttons.

### [1.1.1] — 2026-10-13

#### Fixed
- **Critical: user data was orphaned.** `APP_NAME` had been changed to `"QuanQiuTong"`
  (the *display* name) when the product was renamed. The app then looked in a brand-new
  empty `%APPDATA%\QuanQiuTong` directory, leaving the real `config.json`, `secrets.json`
  (API key) and `memory.db` (153 glossary terms, 61 cache entries, 139 history rows)
  behind — from the user's side, "all my data is gone".
  `APP_NAME` is now `"CS2ChatTranslator"` again, decoupled from `APP_DISPLAY_NAME`,
  with `TestDataDirStability` in place to prevent a repeat.
- Auto-scroll made resilient (see 1.2.0 — the flag-based rewrite landed here first).

#### Added
- `docs/核心邏輯說明.md` — design rationale for every subsystem.

### [1.1.0] — 2026-10-13

#### Added
- Initial public release.
- Read-only `console.log` watching with incremental byte-offset reads.
- Live translation of foreign chat into Simplified Chinese.
- Chinese reply translation with a strict three-state indicator
  (*ready → copied → confirmed sent*); never auto-sends.
- Per-match isolation with a generation counter; late results discarded.
- Six-step lookup chain (manual correction → memory → glossary → templates → cache → API).
- Three-tier retention: current match / match history / long-term memory.
- Startup reset: clears live translations and match history, keeps glossary and memory.
- PySide6 UI with sidebar navigation and dark/light themes.
- CLI mode (`--cli`, `--diagnose`, `--reply`, `--version`).

---

## 简体中文

### [1.2.6] — 2026-10-25

修复 **CS2 无响应（AppHangB1）**：全球通运行时 CS2 周期性卡顿，且全球通自身窗口也会停止响应。

#### 修复
- **全球通运行时 CS2 周期性卡顿。** 引擎线程每 0.5 秒推送一次状态，每次都会触发整套窗口
  刷新 —— 即使 CS2 在前台，窗口仍每秒重绘约 1.9 次。现在 CS2 在前台时状态推送降到每 2 秒
  一次，且该状态下完全不碰 UI；回到桌面补刷一次。**实测：1.9 次/秒 → 0 次/秒。**
- **关闭时全球通自身被判「无响应」。** `engine.stop()` 最坏阻塞 UI 线程 22 秒。
  现在先隐藏窗口、每步上限 1.5 秒、不再等待回调线程池。
- **重复实例。** 第二次启动会唤醒已有窗口并退出，不再多跑一份造成文件轮询与重绘翻倍。
- **日志文件缺失或被重建时按 250 毫秒无限重试。** 现在 250ms → 500ms → 1s → 2s，成功后复位。
- **状态栏每秒查询一次数据库**（句柄数偏高的嫌疑来源）。现在 5 秒缓存，且副标题只在文字
  变化时才重写。
- **打包版无法启动**：瘦身清单删掉了 `QLocalServer` 依赖的 `Qt6Network`。已保留。
- 窗口关闭后才返回的 API 测试结果会抛 `RuntimeError: Signal source has been deleted`。

#### 更新
- **前台感知。** `cs2.exe` 在前台时，日志轮询间隔 0.25 秒 → 1.5 秒，暂停非必要 UI 更新，
  消息仍照常入队，回到桌面一次性补齐渲染。
- 启动自检 API 改用 **5 秒超时且不重试**，不再沿用翻译主路径的 20 秒。
- 打包保留 `qoffscreen` 平台插件，可在不弹出窗口的情况下做自动化验证。

#### 验证
435 项测试通过。CS2 在前台时 UI 刷新实测 **0 次/秒**。已由真实全屏对局确认不再卡顿。

### [1.2.3] — 2026-10-22

#### 新增
- **译文发出后，回复输入框会自动清空。**
  翻译被复制、你粘贴到游戏发出之后，程序会盯着 `console.log` 找你自己那条消息；
  一旦看到，就清空输入框，下一句可以直接打。
  - 两条识别路径：发送者等于新增的「**我的游戏昵称**」设置；或留空时，
    把收到的内容与刚复制出去的译文比对（归一化空白、标点与大小写，允许部分包含）。
  - **防丢数据保护**：只有存在「已翻译待发出」的译文时才清空。
    如果你只是打了个草稿还没翻译，自己在游戏里随手指一句话，草稿不会被清掉。

#### 变更
- 设置 → 语言设置 新增「**我的游戏昵称**」字段（可留空）。

### [1.2.2] — 2026-10-14

#### 变更
- **回覆翻譯流程简化：翻譯成功即自動複製。**
  真实动线是「翻译出来 → 已在剪贴板 → 进游戏 Ctrl+V」，所以「复制译文」按钮与
  「我已发送」确认框都是多余的。按钮改名为「**翻译并复制**」，状态提示改为
  单一状态列（等待 / 翻译中 / 已复制 / 失败），不再三个控件并排。
  - 状态提示**绝不声称已发送**，并明写「本程序不会自动发送」。
- 设置页分组标题移除 `A.` / `B.` / `C.` 编号。

#### 新增
- **启动时自动测试 API** —— 状态列不再一直显示「API 未测试」，
  启动约 1.5 秒后自动测一次（仅在有 API Key 时）。
- **启动时检查更新** —— 查询 GitHub 公开 Releases API 的版本号，
  **不发送任何本地资料**，离线时静默失败，可在设置里完全关闭。

#### 修复
- **翻译记忆页卡顿** —— 与词汇表同一个 O(n²) 陷阱：栏位用 `ResizeToContents` 时，
  Qt 会在**每次** `setItem` 后重算栏宽（64 列 × 7 栏）。重建期间隐藏表格后，
  切页降到 **13.5 ms**。搜寻框加 180ms 防抖，统计改用 SQL 聚合
  （`term_stats()` 与新增的 `template_count()`），不再捞 10 万笔。
- **词汇表与记忆表看起来「很空」** —— `resizeColumnsToContents()` 会把 `Stretch` 栏
  也按内容宽度设置，导致词汇表 6 栏合计只有 480 px，而表格宽 1116 px，
  超过一半是空白。现在只量测 `ResizeToContents` 的栏，两表填充率均为 **100%**。
- **关闭时的竞态崩溃** —— 状态列定时器或搜寻防抖在引擎关闭数据库后触发时，
  会抛 `sqlite3.ProgrammingError: Cannot operate on a closed database`。
  `Database` 现在有 `_closed` 旗标：关闭后读取回传 `None` / `[]`，
  但 `transaction()` 仍明确报错（静默吞掉写入会掩盖真问题）。
  各页面另提供 `stop_timers()`，关闭流程会呼叫并等待更新检查执行绪结束。

### [1.2.1] — 2026-10-14

#### 修复
- **严重：误删 OpenSSL DLL 导致翻译功能全废。**
  v1.2.0 的瘦身误把 `libssl-1_1.dll` / `libcrypto-1_1.dll` 列入排除清单，理由是
  「Python 3.11 使用 OpenSSL 3.x」——该假设是错的。这个 Python 的 `_ssl.pyd`
  直接汇入它们（OpenSSL 1.1.1u）。后果：`_ssl` 载入失败 → `openai` 无法 import →
  翻译完全停止，而 GUI 只显示一行提示。
  - 两个 DLL 已从排除清单移除，并在清单旁留下显眼警告防止重犯。
  - 体积代价：32.52 MB → 34.01 MB（+1.49 MB），仍比原本 54.44 MB 小 37.5%。

#### 新增
- **`--diagnose` 运行环境自检**：报告 `ssl` 与 `openai` 是否真的可用，缺失时给出
  可直接照着做的修复方向：
  ```
  ==== 运行环璄自检 ====
    ssl: OK  OpenSSL 1.1.1u  30 May 2023
    openai: OK  2.54.0
  ```
- **`TestPackagingSpec`（5 项测试）**：锁住「spec 不得排除 OpenSSL DLL / 必需 Qt 模块」，
  并确保警告注解不被删掉。

#### 说明
- 为什么 v1.2.0 的验证没抓到：当时 HTTPS 测试跑在 `.venv` 的 Python 上（有完整
  OpenSSL），而不是打包后的 EXE。现在验证直接针对构建产物本身。

### [1.2.0] — 2026-10-14

#### 新增
- **消息排列设置**：可选「最新在底部 + 自动滚动」（默认）或「最新在顶部」，
  不滚动也不会漏看新消息。
- **全套调色板达 WCAG AA**：实测并修正 15 组配色，其中 8 组原先不合格
  （例如主按钮白字对强调色只有 3.59:1，需 4.5:1；浅色主题 success 只有 3.29:1）。
- **设计 token 统一**：字阶拉开为 11/12/14/16/20/24（原本 13 与 14 只差 1px）、
  间距对齐 4pt 网格、圆角改为 6/10/14。
- **打包瘦身 54.44 MB → 32.52 MB（−40.3%）**，每项排除都有 PE 汇入表分析依据
  （见 README 的「构建」章节）。

#### 变更
- `PageHeader` 不再渲染标题，页面标题统一由外壳顶部栏负责。此前两处都用
  `objectName="pageTitle"`，导致每页出现两次标题、且都被放大到 24px。
- 移除设置页重复的 `section_title("设置")`。
- `QLabel` 默认透明。Qt 在 `QWidget` 设定背景后会给每个标签画一块矩形，而全局
  `QWidget` 颜色（`bg_surface`）比页面底色（`bg_root`）**更深**，
  于是每个标签背后都有一块突兀的深色方块。

#### 修复
- 自动滚动会静默失效：「使用者是否在底部」原本每次都用 `value`/`maximum` 现算，
  但消息刚插入时滚动条范围尚未更新，于是「其实还在底部」被误判成「已离开底部」。
  现改为显式旗标追踪。
- 词彙表效能：栏位用 `ResizeToContents` 时，Qt 会在**每次** `setItem` 后重算栏宽，
  退化成 O(n²)。153 列要 1300–3200 ms，且越切越慢。改为重建期间隐藏表格 +
  只呼叫一次 `resizeColumnsToContents()` → **8–14 ms**。搜寻框另加 180ms 延迟。
- 统计改用 SQL 聚合（`term_stats()`，0.025 ms），不再把 10 万笔捞进 Python 逐笔数。
- 页头副标题与按钮同排时被压成两行的问题。

### [1.1.1] — 2026-10-13

#### 修复
- **严重：使用者资料变成孤儿。** 改产品名时把 `APP_NAME` 一起改成了 `"QuanQiuTong"`
  （那是**显示名**）。程式于是跑去一个全新的空目录 `%APPDATA%\QuanQiuTong`，
  真正的 `config.json`、`secrets.json`（API Key）与 `memory.db`（153 条术语、
  61 条快取、139 条历史）全被留在原地 —— 使用者感受就是「资料全没了」。
  现已把 `APP_NAME` 改回 `"CS2ChatTranslator"`，与 `APP_DISPLAY_NAME` 解耦，
  并加入 `TestDataDirStability` 防止再犯。
- 自动滚动改为旗标追踪（详见 1.2.0）。

#### 新增
- `docs/核心邏輯說明.md` —— 各子系统的设计逻辑与实作决策。

### [1.1.0] — 2026-10-13

#### 新增
- 首次公开发布。
- 只读监听 `console.log`，按字节增量读取。
- 外语聊天 → 简体中文的实时翻译。
- 中文回覆翻译，严格三态指示（已生成 → 已复制 → 已确认发送），绝不自动发送。
- 对局隔离（代际计数，迟到结果丢弃）。
- 六级查找链（人工修正 → 记忆 → 术语 → 模板 → 快取 → API）。
- 三层资料保留：当前局 / 历史对局 / 长期记忆。
- 启动重置：清空实时翻译与历史对局，保留词汇表与记忆库。
- PySide6 界面：左侧导航 + 深色/浅色主题。
- 命令列模式（`--cli`、`--diagnose`、`--reply`、`--version`）。

---

[1.2.6]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.3...v1.2.6
[1.2.3]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Goduzun/QuanQiuTong/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/Goduzun/QuanQiuTong/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Goduzun/QuanQiuTong/releases/tag/v1.1.0
