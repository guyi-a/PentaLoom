---
name: computer-use
description: "macOS 桌面自动化任务 (操作原生 app / 走菜单 / 发系统快捷键 / 切前台 app). 触发场景: 用户说\"打开备忘录\" / \"在系统设置里改 X\" / \"用 Finder 看一下\" / \"按 Cmd+S 保存\" / \"切到 X app\" 等. 走 macOS Accessibility API + CGEvent. 包含强制工作流 (permissions → apps → snapshot / menu → 操作 → 验证) + 五条铁律 + Electron 盲区警告. 不适用: 网页操作 (走 browser_bridge) / 文件读写 (走 file 能力) / 浏览器内任务 (走 browser_bridge 或 browser-use)."
---

# Skill: computer-use (macOS 桌面自动化)

适用场景: 用户要操作 macOS 桌面 / 原生 app / 系统功能 — Finder 文件操作 / 系统设置改配置 / 备忘录写东西 / Music 控制播放 / Mail 查邮件 / 跨 app 走菜单 / 发系统快捷键.

不适用 (走别的能力):
- 网页操作 → `browser_bridge` 或 `browser-use` skill
- 读写文件 → PentaLoom file 能力 (Read / Write / Edit / Glob / Grep)
- 网络请求 → Python 脚本 + httpx

## 工作流 (MUST 顺序)

1. **检查权限**: 首次调任何 action 前先调
   `mcp__pentaloom_computer__computer_use(action="permissions")`:
   - `trusted=true` → 直接进下一步
   - `trusted=false` → 返回 message 里有引导, 把引导文字给用户看, 让他按步骤开权限 (Settings → Privacy & Security → Accessibility), 用户开完告诉你, 再继续
   - 也可以 `permissions(prompt=true)` 主动触发系统授权弹窗, 但需要提示用户去点 "打开系统设置"
2. **了解环境**: `action="apps"` 看正在跑哪些 app, 拿到名字 + pid + 是否前台
3. **选路径**:
   - **走菜单** (推荐, 任何 app 都吃): `action="menu", target="<app名>", path=["文件", "新建文件夹"]`. 不需要 snapshot, 一句话搞定. **Electron app 也能走**
   - **走 snapshot + press** (主内容操作, 只在原生 app 真有效): `action="snapshot", target="<app名>"` 拿元素列表 + snapshot_id, 然后按 index 调 `press` / `set_value`
4. **执行动作**: 按上一步决定调 menu / press / set_value / key 等
5. **验证完成**: 再 `snapshot` 一次, 用元素状态变化 / 新窗口出现 / 焦点变化判断动作真生效 (见铁律 1)
6. **收尾**: 不要主动 close 任何用户 app, 任务完成简要报告即可

## 五条铁律

### 铁律 1: 操作成功 ≠ 任务成功

`press` 返回 `success=true` 只表示 AXPress 调用没崩, **不代表 app 真完成了业务动作**. 例如:
- 点 "保存" 按钮可能弹了"另存为"对话框等用户输入
- 点菜单项可能弹了二级菜单 / 模态对话框 / 确认窗
- 焦点切换可能被别的 app 抢回去

每个关键动作后 **必须再 `snapshot`** 用以下证据确认:
1. 出现预期的新窗口 / 对话框 / 菜单
2. 关键元素的 title / value / focused 变了
3. 没有出现错误 alert

### 铁律 2: 用户阻断 = 立刻终止, 不要替用户决定

弹出以下东西**立刻停**, 把状态告诉用户让他处理:
- **任何登录 / 解锁 / Touch ID 提示**
- **支付 / 银行 / 二次验证窗**
- **删除 / 清空 / 关闭未保存 等不可逆动作的确认窗**
- **系统升级 / 安装 / 重启 等高权限提示**
- **法律条款 / 同意书 / 隐私授权**

bridge 在用户浏览器, computer 在用户**整台电脑** — 误操作风险更大. 这条比 bridge 还要严.

### 铁律 3: index 是瞬态证据, 操作前必重新 snapshot

`snapshot` 返回的 `elements[i].index` 是当次扫描的扁平化序号, **任何 app 状态变化都会让 index 全失效**:
- app 切前台 / 后台
- 弹层 / 关层
- 菜单展开 / 收起
- scroll
- 内容刷新

操作前 90% 的概率要重新 snapshot. 同 verb 多次失败大概率就是 index 过期.

### 铁律 4: 主内容操作只在原生 app 真有效

Phase 0 实测发现:
- **原生 app** (Finder / 系统设置 / 备忘录 / Mail / Music / Calendar / Notes / 终端): AX 树完整, 文件名 / 字段 / 按钮全暴露, 主内容操作没问题
- **Electron app** (VSCode / Cursor / Slack / Discord / Notion / Codex / 飞书 / 微信桌面版): 菜单完整, **主内容区是黑盒** (AXGroup 嵌套, 看不到代码 / 文件树 / 聊天列表 / 标签页)
- **混合 app** (Chrome / Safari): 菜单完整, **网页主区是黑盒** (这是 browser_bridge 的活儿)

对 Electron / Chrome / 微信 / 飞书这类自渲染 app:
- ✅ 能做: 走菜单 (`action="menu"`), 发快捷键 (`action="key"`), 切前台 (`action="focus"`), `Bash open -a "X"` 启动
- ❌ 不要做: snapshot 后想 press / set_value 主内容里的元素 — 拿不到, 浪费用户时间
- 别的事走对应能力: 编辑代码 → file, 网页 → browser_bridge, 聊天/发消息 → **诚实告诉用户当前 M8 做不到, 让用户自己操作或等 M9 视觉能力**

**当前限制 (M8) 必须诚实告诉用户的场景**:
- "在飞书/微信/Slack/Discord 里给 X 发消息" → 只能帮打开 + 切前台, 主聊天区拿不到
- "在 VSCode/Cursor 编辑器主区改代码" → 走 file 能力 (Read/Write/Edit), 不要绕 computer_use
- "在 Notion/Linear/ClickUp 桌面版里操作" → Electron 黑盒同款
- "在 Figma/Sketch 画布上画图" → Canvas 完全没 AX

**反模式**: 看到 Electron app 主区 snapshot 拿到一堆 AXGroup 嵌套 + 空内容 → 不要去**乱猜**索引乱 press; 直接告诉用户 "X 的主聊天区/编辑器区是 Electron 渲染, 我看不到, 请你自己操作". 这是诚实, 不是失败.

### 铁律 5: 危险动作必须事先告知用户 + 等回应

哪怕用户授权了 session, 以下动作做之前必须**明确写一句"我要做 X, 是吗?"等用户确认**:
- 删除文件 / 倒废纸篓 / 清空 (Finder, Mail, Music 等)
- 清空回收站
- 退出 app / 重启 / 关机
- 卸载 app
- 更改系统级设置 (网络 / 防火墙 / 用户账户)
- 任何"无法撤销"的菜单项

这跟 HITL session allowlist 是两层 — allowlist 只是免审 OS dialog, 业务上的不可逆操作 LLM 必须自我克制. 用户授权了"computer-use 整个会话免审"不等于授权了"删我桌面的文件".

## 工具选型: 何时走 AppleScript 而不是 computer-use

**重要**: 不是所有 macOS 任务都该用 computer-use. macOS 的"Scriptable Apps"有自己的 AppleScript 字典, 通过 `osascript` 调用比 AX **更稳、更快、更直接**, 因为它是 Apple 设计的 app 间数据 API, 不依赖 UI 元素能不能被 AX 抓到.

### Scriptable App 列表 (优先 AppleScript, 不用 computer-use 做内部操作)

| App | AppleScript 能做 | 示例 osascript |
|---|---|---|
| **备忘录 Notes** | 增/删/查/改笔记, 文件夹管理 | `tell application "Notes" to make new note with properties {body:"text"}` |
| **邮件 Mail** | 读/写/发送邮件, 查邮箱状态 | `tell application "Mail" to make new outgoing message with properties {subject:"X", content:"Y"}` |
| **音乐 Music** | 播放控制 / 当前曲目 / 播放列表 | `tell application "Music" to play` |
| **提醒事项 Reminders** | 增/删/查提醒 | `tell application "Reminders" to make new reminder with properties {name:"X"}` |
| **日历 Calendar** | 增/查事件 | `tell application "Calendar" to make new event ...` |
| **访达 Finder** | 文件操作 / 选中 / 显示 | `tell application "Finder" to open POSIX file "/path"` |
| **Safari** | tab 操作 / 拿 URL / 跑 JS (但建议用 browser_bridge) | `tell application "Safari" to make new document with properties {URL:"..."}` |

### 系统级 osascript 命令 (优先走 Bash, 不用 computer-use 走菜单)

| 任务 | osascript | 备注 |
|---|---|---|
| 设音量 | `set volume output volume 50` | 0-100 |
| 读音量 | `output volume of (get volume settings)` | |
| 静音/解除 | `set volume with output muted` / `set volume without output muted` | |
| 设系统通知 | `display notification "X" with title "Y"` | 弹一条通知 |
| 关机/重启/睡眠 | `tell application "System Events" to (shut down / restart / sleep)` | **铁律 5 — 必须先问用户** |
| 弹对话框确认 | `display dialog "X" buttons {"OK","Cancel"} default button 1` | 阻塞等用户点 |
| 截屏到剪贴板 | `do shell script "screencapture -c"` | 直接 shell 也行 |
| 解锁屏幕 / Touch ID 提示 | ❌ 故意没接口 | 用户必须手动 |

调用方式: 走 **Bash 工具** (不是 computer-use), 命令体:
```bash
osascript -e 'tell application "Notes" to make new note with properties {body:"测试内容"}'
```
或多行 here-doc:
```bash
osascript <<'EOF'
tell application "Notes"
  activate
  set newNote to make new note with properties {body:"computer-use M8 测试"}
  return id of newNote
end tell
EOF
```

### 怎么决策

**走 computer-use 的场景**:
- 跨 app 协调 (在 X app 干事然后切到 Y app)
- 走菜单完成动作 (任意 app 的 menu 都吃)
- 发系统快捷键 (Cmd+Space / Cmd+Tab / 调亮度等)
- 操作非 Scriptable 的原生 app (系统设置 / 控制中心)
- 操作第三方 GUI app (Sketch / Figma 桌面版 / Bartender 等)

**走 AppleScript via Bash 的场景**:
- 任何 Scriptable App 的内部 CRUD (新建笔记 / 加日历事件 / 播放音乐)
- 需要拿 app 内部结构化数据 (列出所有邮件 / 列出当前播放列表)
- 需要在 app 启动前/后跑动作 (先 activate 再操作)

**走 file 能力的场景**:
- 读写 / 编辑文件本身 (不通过 app UI)

**走 browser_bridge 的场景**:
- 网页操作 (即使 Safari 是 Scriptable, 复杂网页交互还是 bridge 强)

### 反模式

❌ 用 `computer_use(snapshot)` 找 Notes 的输入框然后 `set_value` — AX 在 NSTextView/WebView 上经常失败
✅ 用 `osascript` 一行搞定: `tell application "Notes" to make new note with properties {body:"..."}`

❌ 用 `computer_use(menu, target="Music", path=["Controls", "Play"])`
✅ 用 `osascript`: `tell application "Music" to play`

❌ 用 `computer_use(menu, ...)` 在 Mail 里翻菜单写邮件
✅ 用 `osascript`: `tell application "Mail" to make new outgoing message ...`

## 工具速查

只有一个工具, 用 `action` 分发:

```
computer_use(action="...", 其它参数按 action 需要传)
```

| action | 必传 | 选传 | 用途 |
|---|---|---|---|
| `permissions` | — | `prompt: bool` | 检查 AX 权限. prompt=true 触发系统弹窗 |
| `apps` | — | — | 列正在跑的 app |
| `snapshot` | `target` (app 名/pid) | `depth`, `max_children` | dump AX 树, 返回元素列表 + snapshot_id |
| `menu` | `target`, `path` (string list) | — | 按菜单路径执行, 例: `path=["文件", "新建文件夹"]` |
| `press` | `snapshot_id`, `index` | — | 对元素执行 AXPress (button / menu item / checkbox 等) |
| `set_value` | `snapshot_id`, `index`, `value` | — | 给 textfield / slider 设值 |
| `focus` | `target` (app 名/pid) | — | 把指定 app 切到前台 |
| `key` | `combo` (例: `"cmd+s"`) | — | 发键盘组合, 不靠 AX |

### menu path 写法

`path` 是从菜单栏顶层名字开始的列表. 中文系统就用中文名 (实测 Finder 的 "文件 > 新建文件夹"). 例:

- Finder 新窗口: `path=["文件", "新建访达窗口"]`
- 备忘录新笔记: `path=["文件", "新建备忘录"]`
- Mail 发邮件: `path=["文件", "新邮件"]`
- 系统设置: 一般直接 `focus` 它然后 snapshot 找 sidebar
- 应用菜单 (about/preferences/quit): `path=["<app名>", "关于<app名>"]` / `path=["<app名>", "退出"]`

不确定有什么菜单时, 先 `snapshot(target=app, depth=4)` 看 AXMenuBar 的 AXMenuBarItem 列表.

### key combo 写法

- 单键: `"escape"`, `"return"`, `"tab"`, `"space"`, `"delete"`, `"up/down/left/right"`, `"f1"..."f12"`, 字母数字
- 修饰键: `cmd` / `shift` / `alt` (= `option`) / `ctrl` / `fn`, 用 `+` 连
- 例: `"cmd+s"`, `"cmd+shift+t"`, `"cmd+alt+esc"`, `"cmd+space"`

注意: `key` 走 CGEvent, 不需要 target — 发到当前前台 app. 想发给特定 app 先 `focus` 它.

## 失败处理

| 现象 | 措施 |
|---|---|
| `permissions` 返 trusted=false | 引导用户在系统设置里给宿主进程开权限. 别假装能绕过 |
| `snapshot` 返回元素超少 (< 10) | 大概率 Electron 主区, 试 menu 路径; 还不行交回给用户 |
| `press` 报 "AXPress 失败" | 元素可能 disabled / 已失效, 重 snapshot 拿新 index |
| `menu` 报 "找不到 menu item" | 看返回 message 里 "当前层可选" 列表, 用户语言可能跟你期望不一样 (中文/英文) |
| `key` 没生效 | 检查前台是不是预期 app, 先 `focus` 再发 |
| 某个 app 完全没 AX 反应 | 它可能没有 menubar (后台 app / 全屏游戏), 这类不在本工具能力范围 |

## 跟 browser_bridge 的差异速查

| 维度 | computer-use | browser_bridge |
|---|---|---|
| 对象 | 整台 macOS / 所有原生 app | 用户的 Chrome 浏览器 |
| 数据源 | AX 树 (macOS API) | bridge markdown (Chrome 扩展) |
| 元素定位 | index + AXPress / AXSetValue | index + AXPress-like 但走扩展 |
| 跨 app | ✅ 任意切 | ❌ 只在 Chrome |
| 主内容操作 | 原生 app ✅, Electron/混合 ❌ | ✅ 网页全可用 |
| 菜单 | ✅ 任何 app | ❌ 不操作浏览器菜单 |
| 系统快捷键 | ✅ key API | ❌ |
| 平台 | 仅 macOS | 跨 OS (扩展决定) |

## 给用户看的总结

任务完成时简要报告:
- 做了什么 (在 X app 走了菜单 / 改了什么 / 发了什么快捷键)
- 现在状态 (哪个 app 在前台 / 用户能看到什么)
- 还需用户做什么 (有时候要校验 / 确认 / 手动收尾)

不要把 raw snapshot tree 原样贴给用户.
