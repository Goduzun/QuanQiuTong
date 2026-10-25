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

**The 1.2.5 "pause UI updates" fix did not actually work.** Measured and fixed.

#### Root cause of the remaining hang
`engine._status_loop` pushes a status snapshot **every 0.5 s** on its own thread, and
`MainWindow._on_status` turned each one into a full `_refresh_status()`. So while `cs2.exe`
was in the foreground, stopping the UI's own 1 s timer changed nothing — the window still
repainted ~1.9 times per second. That is why CS2 kept hanging (an AppHangB1 was still
recorded at 01:35 on the 1.2.5 build).

#### Fixed
- **Status pushes are throttled while CS2 is in the foreground** (0.5 s → 2 s), and
  `_on_status` no longer touches the UI at all in that state; one catch-up refresh runs
  when you come back.
- Late `api_test_done` / `log_test_done` results arriving after the window closed no longer
  raise `RuntimeError: Signal source has been deleted`.

#### Measured (offscreen, counting real calls)
| Mode | UI refreshes | engine status pushes |
|---|---|---|
| normal | 3.00 /s | 1.88 /s |
| CS2 foreground | **0.00 /s** | 0.50 /s |
| back to desktop | 1 immediate catch-up | — |

#### ✅ Confirmed fixed in a real play session
The reporter played a full-screen session on this build and confirmed the stutter is gone.
This is the first release in the series where the fix was validated against real gameplay
rather than only against measurements — 1.2.5 had already claimed a fix and had not
actually stopped the repaints.

### [1.2.5] — 2026-10-25

Fixes for the reported **CS2 AppHangB1 / "not responding"** issue.

> Supersedes an unreleased 1.2.4 local build. 1.2.4 contained the fixes below but
> introduced a hang on *its own* shutdown (UI thread blocked up to 22 s); 1.2.5 fixes that.

#### Added
- **Single-instance lock.** A second launch now wakes the existing window and exits
  instead of running a duplicate. Two instances had been observed running side by side
  (one accumulated 877 s of CPU over 2 days), doubling file polling and UI redraws.
- **Foreground awareness.** While `cs2.exe` is the foreground window the app raises its
  log poll interval (0.25 s → 1.5 s), stops the 1 s status refresh, and defers rendering.
  Messages are still queued and are rendered in one pass when you come back.

#### Changed
- Startup API self-check uses a **5 s timeout with no retries** instead of reusing the
  20 s translation timeout.
- Memory statistics are cached for 5 s instead of querying SQLite every second, and the
  header subtitle is only rewritten when the text actually changes. (The 1 s cadence was a
  likely contributor to the 1989-handle count seen on site.)

#### Fixed
- **Log reads back off on failure.** A missing or recreated `console.log` used to be
  retried every 250 ms indefinitely; now 250 ms → 500 ms → 1 s → 2 s, reset on success.
- **Shutdown no longer blocks the UI thread.** `engine.stop()` could block for 22 s
  (10 s watcher + 10 s queue + pool join), which Windows reports as AppHang — this is what
  produced `程序全球通.exe 停止与 Windows 交互并已关闭`. The window is now hidden first,
  each step is capped at 1.5 s, and the callback pool is no longer waited on.
- Packaging: `Qt6Network` had been stripped by the slimming list, which breaks
  `QLocalServer` and made the packaged build fail at import. It is kept now, together with
  the tiny `qoffscreen` platform plugin (enables headless verification without popping
  windows onto the desktop).

#### Note on root cause
Event-log correlation over 7 days shows `cs2.exe` **also hung while this app was not
running at all** (2026-10-20 21:45). The changes above substantially reduce this app's
footprint, but CS2 hangs are not attributable to this app alone.

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

**1.2.5 的「暂停 UI 更新」其实没有真正生效。** 本次量测并修复。

#### 残留卡顿的真正原因
`engine._status_loop` 在自己的线程里**每 0.5 秒**推一次状态快照，
`MainWindow._on_status` 每收到一次就做整套 `_refresh_status()`。
所以当 `cs2.exe` 在前台时，只停掉 UI 自己的 1 秒 QTimer 根本没有用 ——
窗口仍然每秒重绘约 1.9 次。这就是为什么 1.2.5 版本下 CS2 依旧卡顿
（01:35 仍然记录了 AppHangB1）。

#### 修复
- **CS2 在前台时状态推送降频**（0.5 秒 → 2 秒），并且在这种状态下
  `_on_status` 完全不碰 UI；回到桌面时补刷一次。
- 窗口关闭后才到达的 `api_test_done` / `log_test_done` 结果，
  不再抛出 `RuntimeError: Signal source has been deleted`。

#### 实测（offscreen，统计真实调用次数）
| 模式 | UI 刷新 | 引擎状态推送 |
|---|---|---|
| 正常 | 3.00 次/秒 | 1.88 次/秒 |
| CS2 在前台 | **0.00 次/秒** | 0.50 次/秒 |
| 切回桌面 | 立即补刷 1 次 | — |

#### ✅ 已由真实游玩确认修复
使用者用此版本打了一整局全屏，确认不再卡顿。
这是本系列**第一个拿真实对局验证过**的版本 —— 1.2.5 当初也声称修好了，
但实际上并没有真正停掉重绘。

### [1.2.5] — 2026-10-25

修复反馈的 **CS2 AppHangB1 /「无响应」** 问题。

> 取代未发布的本地 1.2.4。1.2.4 已含下方修复，但引入了**自身关闭时卡死**
> （UI 线程被阻塞最长 22 秒）；1.2.5 修掉这个问题。

#### 新增
- **单实例锁。** 第二次启动会唤醒已有窗口并自行退出，不再跑重复实例。现场曾同时
  运行两份（其中一份 2 天累计 877 秒 CPU），文件轮询与 UI 重绘都翻倍。
- **前台感知。** `cs2.exe` 在前台时，日志轮询间隔从 0.25 秒提到 1.5 秒、停掉每秒的
  状态刷新、并延后渲染；消息仍照常入队，回到桌面一次性补齐。

#### 变更
- 启动自检 API 改用 **5 秒超时且不重试**，不再沿用翻译主路径的 20 秒。
- 缓存统计改为 5 秒缓存，不再每秒查一次 SQLite；副标题只在文字真的变化时才重写。
  （每秒一次的节奏是现场 1989 个句柄的嫌疑来源之一。）

#### 修复
- **日志读取失败会指数退避。** 原本文件缺失或被重建时按 250 毫秒无限重试；
  现在 250ms → 500ms → 1s → 2s，成功后复位。
- **关闭不再阻塞 UI 线程。** `engine.stop()` 原本最坏阻塞 22 秒
  （watcher 10 秒 + 队列 10 秒 + 线程池等待），Windows 会判为 AppHang ——
  这正是事件日志里「程序全球通.exe 停止与 Windows 交互并已关闭」的来源。
  现在先隐藏窗口、每步上限 1.5 秒、不再等待回调线程池。
- 打包：瘦身清单误删了 `Qt6Network`，导致 `QLocalServer` 不可用、打包版启动即
  ImportError。现已保留，并额外保留极小的 `qoffscreen` 平台插件
  （可在不弹出窗口的情况下做自动化验收）。

#### 关于根因
比对近 7 天事件日志：**本程序完全没运行时，`cs2.exe` 也发生过 AppHang**
（2026-10-20 21:45）。上述改动大幅降低了本程序的占用，但 CS2 卡顿不能单独归因于本程序。

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

[1.2.6]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.5...v1.2.6
[1.2.5]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.3...v1.2.5
[1.2.3]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/Goduzun/QuanQiuTong/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Goduzun/QuanQiuTong/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/Goduzun/QuanQiuTong/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Goduzun/QuanQiuTong/releases/tag/v1.1.0
