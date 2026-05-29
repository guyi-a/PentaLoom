---
name: computer-use
description: "macOS 桌面自动化任务 (操作原生 app / 走菜单 / 发系统快捷键 / 切前台 app / 截图 + 鼠标点击 + 粘贴 Electron 主区). 触发场景: 用户说\"打开备忘录\" / \"在系统设置里改 X\" / \"用 Finder 看一下\" / \"按 Cmd+S 保存\" / \"切到 X app\" / \"在飞书发条消息\" / \"在 Notion 写一段\" 等. M8 走 macOS Accessibility API + CGEvent, M9 加 Quartz 截图 + 鼠标 + 粘贴 (Electron 主区兜底). 包含强制工作流 (permissions 双权限 → apps → snapshot 或 screenshot → 操作 → 验证) + 七条铁律 + Electron 视觉兜底路径. 不适用: 网页操作 (走 browser_bridge) / 文件读写 (走 file 能力) / 浏览器内任务 (走 browser_bridge 或 browser-use)."
---

# Skill: computer-use (macOS 桌面自动化)

适用场景: 用户要操作 macOS 桌面 / 原生 app / 系统功能 / Electron app 主区 — Finder 文件操作 / 系统设置改配置 / 备忘录写东西 / Music 控制播放 / Mail 查邮件 / 跨 app 走菜单 / 发系统快捷键 / **在飞书 Slack Notion 等 Electron 主区交互 (M9 视觉路径)**.

不适用 (走别的能力):
- 网页操作 → `browser_bridge` 或 `browser-use` skill
- 读写文件 → PentaLoom file 能力 (Read / Write / Edit / Glob / Grep)
- 网络请求 → Python 脚本 + httpx

---

## 12 个 action 速查

只有一个工具, 用 `action` 分发: `computer_use(action="...", ...)`

| action | 必传 | 选传 | 用途 |
|---|---|---|---|
| `permissions` | — | `prompt: bool` | 检查 Accessibility + Screen Recording 双权限. prompt=true 各自触发系统弹窗 |
| `apps` | — | — | 列正在跑的 app |
| `snapshot` | `target` (app 名/pid) | `depth`, `max_children` | dump AX 树, 返元素列表 + snapshot_id |
| `menu` | `target`, `path` (list) | — | 按菜单路径执行, 例: `path=["文件", "新建文件夹"]` |
| `press` | `snapshot_id`, `index` | — | 对元素执行 AXPress (button / menu item / checkbox 等) |
| `set_value` | `snapshot_id`, `index`, `value` | — | 给 textfield / slider 设值 |
| `focus` | `target` (app 名/pid) | — | 把指定 app 切到前台 |
| `key` | `combo` (例: `"cmd+s"`) | — | 发键盘组合, 不靠 AX |
| **`screenshot`** | — | `target`, `scale=0.33`, `quality=70`, `format="jpeg"` | 截图. target="screen" 整 desktop 跨所有屏 (默认, 多屏 ~2-2.5x token); "main_display" 只主屏 (~1.1k); "&lt;app名&gt;" 单 app 第一窗口 (跨屏有效) |
| **`mouse_move`** | `x`, `y` (逻辑像素) | — | 把鼠标移到 (x, y), 不点击 |
| **`mouse_click`** | `x`, `y` | `kind="single"\|"double"\|"right"` | 在 (x, y) 点击, 自动触发屏幕涟漪 overlay |
| **`paste`** | `text` | — | 写剪贴板 + 发 Cmd+V, 完后自动恢复用户原剪贴板. CJK + emoji 完美 |

---

## 工作流 (MUST 顺序)

### 通用启动 (任何任务前)

1. **检查双权限**: `computer_use(action="permissions")`. 看返回:
   - `accessibility.trusted = false` → 引导用户开 Accessibility (AX / mouse / key 都依赖)
   - `screen_recording.trusted = false` → 引导用户开 Screen Recording (screenshot 依赖)
   - 缺权限时可调 `permissions(prompt=true)` 触发系统弹窗
2. **了解环境**: `action="apps"` 看正在跑哪些 app

### AX 路径 (原生 app / 走菜单 — 最稳)

3. **走菜单** (任何 app 都吃, 推荐): `action="menu", target="<app名>", path=["文件", "新建"]`
   或
4. **走 snapshot + press** (主内容操作, 仅原生 app 真有效): `action="snapshot"` 拿元素 + snapshot_id, 按 index 调 `press` / `set_value`
5. **验证**: 再 `snapshot` 用元素变化判断动作真生效 (见铁律 1)

### 视觉路径 (Electron 主区 / Chrome 网页区 / Canvas — M9 兜底)

3'. **截图**: `screenshot(target="<app名>")` — 拿到一张 ~1.1k token 的图
4'. **推理坐标**: 看截图找到目标元素, 算它的**逻辑像素**坐标. 用响应里的 `note` 公式: `logical_x = image_x * (logical_w / scaled_w)`
5'. **操作**: `mouse_click(x, y)` 点过去 (屏幕红涟漪一闪, 用户能看到); 要输文本就 `paste(text=...)`
6'. **验证**: 再 `screenshot` 一次, 用画面变化确认动作生效

### 收尾

不要主动 close 任何用户 app, 任务完成简要报告即可.

---

## 七条铁律

### 铁律 1: 操作成功 ≠ 任务成功

`press` 返回 `success=true` 只表示 AXPress 调用没崩, `mouse_click` 返 success 只代表 CGEvent post 了, **不代表 app 真完成了业务动作**. 例如:
- 点 "保存" 按钮可能弹了"另存为"对话框等用户输入
- 点菜单项可能弹了二级菜单 / 模态对话框 / 确认窗
- 焦点切换可能被别的 app 抢回去
- 视觉点击的坐标可能漂了 (动画 / 弹层位移)

每个关键动作后 **必须再 `snapshot` 或 `screenshot`** 用以下证据确认:
1. 出现预期的新窗口 / 对话框 / 菜单
2. 关键元素的 title / value / focused 变了 (snapshot)
3. 截图里的视觉状态变了 (按钮高亮 / 列表新增项 / 输入框有内容)
4. 没有出现错误 alert

### 铁律 2: 用户阻断 = 立刻终止, 不要替用户决定

弹出以下东西**立刻停**, 把状态告诉用户让他处理:
- **任何登录 / 解锁 / Touch ID 提示**
- **支付 / 银行 / 二次验证窗**
- **删除 / 清空 / 关闭未保存 等不可逆动作的确认窗**
- **系统升级 / 安装 / 重启 等高权限提示**
- **法律条款 / 同意书 / 隐私授权**

bridge 在用户浏览器, computer 在用户**整台电脑** — 误操作风险更大. 这条比 bridge 还严.

### 铁律 3: index 和坐标都是瞬态证据, 操作前重新拍

`snapshot` 返回的 `elements[i].index` 是当次扫描的扁平化序号, **任何 app 状态变化都会让 index 全失效**:
- app 切前台 / 后台
- 弹层 / 关层
- 菜单展开 / 收起
- scroll
- 内容刷新

视觉路径的坐标更瞬态 — scroll / 弹层 / 动画 / 窗口移动都让上一次截图里的坐标作废. **操作前 90% 概率要重新 snapshot / screenshot**. 同操作多次失败大概率就是过期.

### 铁律 4: Electron 主区: AX 不行就走视觉, 别硬撑

| App 类型 | AX 主区 | 走法 |
|---|---|---|
| 原生 app (Finder / 系统设置 / 备忘录 / Mail / Music / Calendar / Notes / 终端) | 完整 | snapshot + press / set_value |
| Electron app (VSCode / Cursor / Slack / Discord / Notion / 飞书 / 微信桌面版) | 黑盒 | **screenshot + mouse_click + paste** ← M9 新路径 |
| 混合 app (Chrome / Safari) 主网页区 | 黑盒 | 优先 `browser_bridge` skill; 不便利时回退视觉 |
| Canvas 类 (Figma / Sketch) | 空白 | 视觉路径; 复杂手势 (拖动) 当前 v2 才有, 先告诉用户 |

**所有 app 的菜单都可用 menu action** — 不分原生 / Electron / Canvas. 菜单是任何 app 通用最稳的入口.

**反模式 (M8 时代的, 现在不再适用)**:
- ❌ 看到 Electron 主区 → 告诉用户 "我看不到, 你自己来吧"
- ✅ 看到 Electron 主区 → screenshot → 推坐标 → mouse_click + paste

**新反模式 (M9 要避免)**:
- ❌ 视觉路径里乱 `mouse_click` 不存在的坐标 (没截图先看一眼就硬点)
- ❌ 用 `mouse_move` 当 hover 探测 (鼠标移过去什么都不会发生, app 不会响应; 要看反馈得截图)
- ❌ 输文本用 `key` 一字一字按 (中文 IME 直接吞键码) — 用 `paste`

### 铁律 5: 危险动作必须事先告知用户 + 等回应

哪怕用户授权了 session, 以下动作做之前必须**明确写一句"我要做 X, 是吗?"等用户确认**:
- 删除文件 / 倒废纸篓 / 清空 (Finder, Mail, Music 等)
- 清空回收站
- 退出 app / 重启 / 关机
- 卸载 app
- 更改系统级设置 (网络 / 防火墙 / 用户账户)
- 任何"无法撤销"的菜单项
- **mouse_click 到不确定的危险位置** (比如截图里看到"删除"按钮但不确定是否目标)

allowlist 只是免审 OS dialog, 业务上的不可逆操作 LLM 必须自我克制. "computer-use 整个会话免审"不等于"删我桌面的文件".

### 铁律 6: 双权限链路 — Accessibility 和 Screen Recording 是分开的

macOS 的 TCC 模型把两类权限独立放:
- **Accessibility**: AX 树 / `mouse_*` / `key` 都依赖. M8 用的就这个.
- **Screen Recording**: `screenshot` 独家依赖. M9 才需要.

两类都给到 "启动 python 的 GUI 宿主进程" (Terminal / iTerm / VSCode / Electron). 同一宿主**要分别授权两次** — 给了 Accessibility 不等于给了 Screen Recording. `permissions` action 同时报告两类状态.

切宿主就要重新授权 (从 Terminal 跑 → 给 Terminal; 切到从 VSCode 跑 → 给 VSCode).

Screen Recording 权限变化**通常要重启宿主进程**才生效 (跟 Accessibility 不同, 后者实时生效). 用户开了之后跑 `screenshot` 还失败 → 让用户重启 VSCode / Terminal 再试.

### 铁律 7: 坐标系 — mouse 用逻辑像素, 截图给物理像素, LLM 自己换算

macOS 有两套像素:
- **逻辑像素** = NSScreen.frame() = mouse / overlay / 窗口位置用的
- **物理像素** = Retina 屏的真实 pixel 数 = 逻辑像素 × scale (Retina 通常 2x)

`screenshot` 返回的 image 是**物理像素 × scale_applied** (默认 0.33). 例如主屏 1728×1117 逻辑 / 3456×2234 物理, scale 0.33 → 你看到的图是 1140×737.

**LLM 必须自己换算**:
```
logical_x = image_x * (logical_px.w / scaled_px.w)
logical_y = image_y * (logical_px.h / scaled_px.h)
```

直接照 `ScreenshotResult.note` 抄, 里面给出本次截图的具体倍数. 别拿截图坐标硬塞给 mouse_click — 物理像素 ≠ 逻辑像素, 会点偏.

---

## 视觉操作 SOP (Electron 主区 / Canvas 必读)

### 5 步循环

1. **screenshot(target=app名)** — 拿当前画面 (默认 jpeg q70 + 0.33x, ~1.1k token)
2. **描述给自己看** — 用文字写出"画面里有什么 / 目标元素在哪个区域 / 截图坐标大约 (image_x, image_y)"
3. **算逻辑坐标** — 用响应里的公式把 image 坐标换成 logical 坐标
4. **mouse_click(x, y) / paste(text)** — 操作 (click 时屏幕涟漪用户能看到)
5. **再 screenshot 验证** — 确认画面变成预期 (按钮高亮 / 弹层出现 / 输入框有字)

### 截图参数选择

- 默认 (`scale=0.33, quality=70, format="jpeg"`) — 看清按钮 / 一行文字 OK, 看 12px 以下小字 (浏览器地址栏 / 紧凑表格) 会模糊
- 需要看小字时升一档: `scale=0.5, quality=85` (~2.6k token, 主屏逻辑分辨率)
- 极省 token 粗略定位: `scale=0.25` (~640 token, 只看大区块)
- 无损模式 (要 zoom 看像素): `format="png"` (约 10x token, 慎用)
- **多屏**: `target="screen"` 拼所有屏成一张图 (双屏 ~2-2.5k token). 只关心主屏用 `target="main_display"` 省回 1.1k. 知道目标 app 在哪用 `target="<app名>"` 截单 app 窗口最省 (单 app 跨屏有效).

### 多屏坐标定位

`target="screen"` 多屏时 response 的 `displays` 列每个屏的 logical 范围 + `note` 给换算公式. 例 (主屏 1728 宽, 副屏 1920 宽接右边):
```
displays = [
  {is_main: true,  logical_origin: (0, 0),    logical_size: (1728, 1117)},
  {is_main: false, logical_origin: (1728, 0), logical_size: (1920, 1080)}
]
```
图里看到飞书在右半 → image_x ≈ 5000 → logical_x = 5000 * 1.5156 ≈ 7578 → 落不到任何 display, 说明算错了; image_x ≈ 1500 → logical_x ≈ 2273 → 落在副屏 (1728 ≤ 2273 < 3648), 是副屏内的局部坐标 = 2273 - 1728 = 545. mouse_click(2273, y) 直接传 desktop 坐标, CGEvent 跨屏 OK.

### 文本输入: paste 优先, 永不 type

- ✅ `paste(text="hi 你好 🚀")` — 系统级粘贴, CJK / emoji / 多行完美
- ❌ 不存在的 type_text — 直接 `CGEventKeyboardSetUnicodeString` 在中文 IME 激活时会丢键
- 工具内部已经 backup + restore 用户剪贴板, 不会污染

### 焦点状态确认

`paste` 需要焦点在某个输入框. 如果不确定:
1. 先 `screenshot` 看光标在哪 (输入框有 caret 闪烁线)
2. 没在输入框 → 先 `mouse_click(输入框坐标)` 把焦点放过去
3. 再 `paste(text)`

### 鼠标互斥提醒 (重要 UX 规约)

agent 操作鼠标 (`mouse_move` / `mouse_click`) 时**抢用户的系统光标** — 用户和 agent 不能同时动鼠标, 不然光标位置冲突, agent 算的坐标就点偏了.

启动视觉操作链路前 (准备连续多次 mouse_click 推进任务), 必须先发一句给用户:
> 我接下来要操作你的鼠标做 N 步, 请暂时不要动鼠标 / 触控板, 看红涟漪就是我在点.

短任务 (1-2 click) 可以不提示, 直接跑. 中长任务 (5+ click) 强制提示. 看到用户动鼠标导致截图 + 点击错位时也要停下来重新提示.

---

## 工具选型: 何时走 AppleScript 而不是 computer-use

**重要**: 不是所有 macOS 任务都该用 computer-use. macOS 的"Scriptable Apps"有自己的 AppleScript 字典, 通过 `osascript` 调用比 AX / 视觉**更稳、更快、更直接**, 因为它是 Apple 设计的 app 间数据 API, 不依赖 UI 元素能不能被 AX 抓到 / 视觉能不能看清.

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
| 截屏到剪贴板 | `do shell script "screencapture -c"` | 系统剪贴板; 想给 LLM 看就用 computer_use(action="screenshot") |
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
- **Electron app 主区 (飞书 / Slack / Notion / Discord / 微信)** ← M9 新场景, 走 screenshot + mouse_click + paste

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

❌ 用 `computer_use(screenshot + mouse_click)` 操作 Finder (有 AX!)
✅ 用 `computer_use(snapshot + press)` 或 `osascript`

---

## menu path 写法

`path` 是从菜单栏顶层名字开始的列表. 中文系统就用中文名 (实测 Finder 的 "文件 > 新建文件夹"). 例:

- Finder 新窗口: `path=["文件", "新建访达窗口"]`
- 备忘录新笔记: `path=["文件", "新建备忘录"]`
- Mail 发邮件: `path=["文件", "新邮件"]`
- 系统设置: 一般直接 `focus` 它然后 snapshot 找 sidebar
- 应用菜单 (about/preferences/quit): `path=["<app名>", "关于<app名>"]` / `path=["<app名>", "退出"]`

不确定有什么菜单时, 先 `snapshot(target=app, depth=4)` 看 AXMenuBar 的 AXMenuBarItem 列表.

## key combo 写法

- 单键: `"escape"`, `"return"`, `"tab"`, `"space"`, `"delete"`, `"up/down/left/right"`, `"f1"..."f12"`, 字母数字
- 修饰键: `cmd` / `shift` / `alt` (= `option`) / `ctrl` / `fn`, 用 `+` 连
- 例: `"cmd+s"`, `"cmd+shift+t"`, `"cmd+alt+esc"`, `"cmd+space"`

注意: `key` 走 CGEvent, 不需要 target — 发到当前前台 app. 想发给特定 app 先 `focus` 它.

## screenshot 入参细节

```
computer_use(action="screenshot",
             target="screen" | "<app名>" | "<pid>",
             scale=0.33,       # 0.05-1.0; 默认 0.33 (~1.1k token)
             quality=70,       # 1-100 (JPEG); PNG 忽略; 默认 70
             format="jpeg")    # "jpeg" / "png"; 默认 jpeg
```

工具返**真正的 image** 给你 see + 一段 metadata text:
- image: 缩放后的 JPEG/PNG, 直接出现在 tool_result 里, 你 (LLM) 能直接看到画面
- metadata text (JSON): `physical_px / logical_px / scaled_px / scale_applied / note` — 坐标换算用

⚠️ **不要**自己写脚本把 image_b64 解码到文件再 Read — 工具已经把图直接给你了, Read 是浪费 4-5 个 tool call.

⚠️ Screen Recording 权限缺失时报错 "CGImage 创建失败...可能缺 Screen Recording 权限". 看到这条 → 调 `permissions(prompt=true)` 引导用户.

## mouse_click 入参细节

```
computer_use(action="mouse_click",
             x=864, y=558,         # 逻辑像素 (跟 NSScreen.frame() 一致)
             kind="single"         # "single" / "double" / "right"
)
```

每次 click 自动触发屏幕涟漪 overlay — 用户能看到 agent 点了哪. 你不用关心 overlay 怎么画, 关心点击坐标对不对.

⚠️ 跨屏 mouse: 副屏逻辑坐标 x 从主屏宽度起 (例: 主屏 1728 宽, 副屏点击用 x=2000). screenshot 当前只截主屏, 副屏视觉操作 v2 再补.

## paste 入参细节

```
computer_use(action="paste", text="...")
```

- text 任意 unicode (CJK + emoji + 多行都行)
- 工具自动备份用户原剪贴板 → 写新值 → 发 Cmd+V → 恢复
- 焦点必须先在某个输入框 (不在的话发了 Cmd+V 会被前台 app 当快捷键处理)

---

## 失败处理

| 现象 | 措施 |
|---|---|
| `permissions.accessibility.trusted=false` | 引导用户在系统设置里给宿主进程开 Accessibility. 别假装能绕过 |
| `permissions.screen_recording.trusted=false` | 引导用户开 Screen Recording **+ 重启宿主进程** (不重启不生效, 跟 AX 不同) |
| `snapshot` 返回元素超少 (< 10) | Electron 主区 — 切视觉路径 (screenshot + mouse_click + paste) |
| `press` 报 "AXPress 失败" | 元素可能 disabled / 已失效, 重 snapshot 拿新 index |
| `menu` 报 "找不到 menu item" | 看返回 message 里 "当前层可选" 列表, 用户语言可能跟你期望不一样 (中文/英文) |
| `key` 没生效 | 检查前台是不是预期 app, 先 `focus` 再发 |
| `screenshot` 返 "CGImage 创建失败" | 大概率 Screen Recording 权限没开; 调 `permissions(prompt=true)` |
| `mouse_click` 点了没反应 | 截图后看坐标对不对; 算清 logical = image * (logical_w / scaled_w) |
| `paste` 后输入框还是空 | 焦点没在输入框 — 先 mouse_click 输入框, 再 paste |
| 某个 app 完全没 AX 反应 + 截图也是空 | 它可能没有 menubar + 没渲染 (后台 app / 全屏游戏), 这类不在本工具能力范围 |

---

## 跟 browser_bridge 的差异速查

| 维度 | computer-use | browser_bridge |
|---|---|---|
| 对象 | 整台 macOS / 所有原生 + Electron app | 用户的 Chrome 浏览器 |
| 数据源 | AX 树 + 截图 (M9) | bridge markdown (Chrome 扩展) |
| 元素定位 | AX index (snapshot) 或 截图坐标 (M9) | bridge index |
| 跨 app | ✅ 任意切 | ❌ 只在 Chrome |
| 主内容操作 | 原生 ✅ AX; Electron / Canvas ✅ 视觉 | ✅ 网页全可用 |
| 菜单 | ✅ 任何 app | ❌ 不操作浏览器菜单 |
| 系统快捷键 | ✅ key API | ❌ |

| 平台 | 仅 macOS | 跨 OS (扩展决定) |

---

## 给用户看的总结

任务完成时简要报告:
- 做了什么 (在 X app 走了菜单 / 改了什么 / 发了什么快捷键 / 截图 + 点 + 粘贴了什么)
- 现在状态 (哪个 app 在前台 / 用户能看到什么)
- 还需用户做什么 (有时候要校验 / 确认 / 手动收尾)

不要把 raw snapshot tree 或截图原图原样贴给用户.
