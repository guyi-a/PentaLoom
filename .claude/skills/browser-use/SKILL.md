---
name: browser-use
description: "浏览器自动化任务 (登录 / 搜索 / 抓取 / 截图 / 表单 / 下载 / 测试 web 页面). 触发场景: 用户说\"打开网站\" / \"帮我登录\" / \"截图\" / \"提交表单\" / \"抓这个页面\" / \"在 X 网站上做 Y\" 等. 走 browser-use CLI 子进程 + 系统 Chrome (或 Playwright Chromium) 兜底. 包含强制工作流 (check 环境 → open → state → 操作 → verify) + 五条铁律 + 元素定位升级路径. 不适用: 纯命令行任务 / 不涉及浏览器的网络请求 (用 curl/Python httpx)."
---

# Skill: browser-use (浏览器自动化)

适用场景: 用户要在浏览器里完成事情 — 登录 / 检索 / 抓取 / 截图 / 填表 / 下载 / 验证 web 页面交互.

## 工作流 (MUST 顺序)

1. **环境检查**: 第一次用浏览器先调 `mcp__pentaloom_browser__install_browser_use(step='check')`. 按返回的 `next_step` 装齐:
   - `next_step="install"` → 调 `install_browser_use(step='install')` 装 browser-use 包
   - `next_step="chromium"` → 调 `install_browser_use(step='chromium')` 装 Playwright Chromium
   - `next_step=null` → 已就绪, 直接 open
2. **打开页面**: `browser_use(command='open https://example.com')`. `--session` 不要手传 — 工具自动注入. `--headed` 默认就开, 用户能看到浏览器窗口.
   - 多账号隔离场景才传 `--profile <name>` (用户可读名: work / personal / dev). 否则不要加 — 跟 Playwright Chromium 不兼容会失败, 且会被存到 session 配置里影响后续命令.
3. **观察状态**: `browser_use(command='state')` 拿当前页 DOM 摘要 + 元素索引. **state 输出仅用于定位**, 不能当最终答案 (见铁律 4).
4. **执行动作**: 按 state 给的 index 调 `click N` / `type N "text"` / `eval ...` / `screenshot foo.png` / `scroll` 等.
5. **验证完成**: 操作后一定再 `state` 一次, 用页面文字 / URL 变化判断动作是否真生效 (见铁律 1).
6. **收尾**: **默认不关浏览器**, 让用户能继续自己看 / 操作页面. 任务结果交付时跟用户说"浏览器我留着了, 你可以继续看; 不需要了告诉我 close". 只有以下情况主动 close:
   - 用户明确说"关掉" / "完事了" / "可以关了"
   - 浏览器明显挂了 / 卡死, 需要 close 重开恢复
   - 当前会话已经聊到完全无关的话题, 用户不再回到浏览器
   close 前工具自动导出 cookies, 下次 open 自动重导, 登录态不丢. 即使用户直接关闭 PentaLoom 会话, browser-use 后台进程也会留着 (用户可以手动关), 下次 open 同 sid 复用.

## 五条铁律

### 铁律 1: 操作成功 ≠ 任务成功

CLI 返回 `exit=0` **只表示命令执行了**, 不代表网站完成业务动作.

- 点了"登录"按钮不代表登录成功 — 可能弹了 CAPTCHA / 密码错 / 2FA 等
- 点了"购买"按钮不代表下单成功 — 可能跳支付页 / 库存不足 / 表单校验红
- `type` 完文字也只是注入了 DOM, 不代表表单 submit

**每个关键动作之后**必须再 `state`, 用以下任一证据确认:
1. URL 变成了预期的下游页 (如 `/login` → `/dashboard`)
2. 页面出现明确的成功标志 (如 "登录成功" / "订单号 #..." / 用户头像出现)
3. 出现明确的失败信号 (错误提示 / 表单红框) — 别假装没看见

证据不齐, 不能向用户报告 "已完成".

### 铁律 2: 用户阻断 = 立刻终止, 不绕过

遇到以下场景, **立刻停止操作**, 把状态如实告诉用户让他接手:

- 登录页要求账号密码 → 不要凭空猜 / 试 / 注册
- CAPTCHA / reCAPTCHA / 图形验证码 → 不要尝试自动识别
- 2FA / 短信验证码 / 邮箱验证链接 → 等用户提供
- 银行 / 支付 / 实名验证页 → 不要替用户操作
- 任何"我同意"的法律条款 / TOS → 必须用户自己点

把当前页 URL + 阻断原因 + 你需要用户做什么, 写成一段清楚的请求. 用户处理完再继续.

### 铁律 3: URL 未变 + 页面未变 → 必须检查其它标签页

点击有些链接会**新开 tab**, 不在当前 tab 加载. 如果点击后 `state` 显示 URL 跟点之前一样、页面内容也没变, **别立刻重试同一个 click**.

- 调 `browser_use(command='state')` 看看 tab 数量
- 用 `switch <index>` 遍历**所有**可能的 tab 索引, 不要看到第一个无关 tab 就回头
- 找到目标后再操作

工具会在 switch 输出前 prepend STOP 警告, 看到要认真处理.

### 铁律 4: state 索引是瞬态证据, 不准存活到下一次操作

`state` 给的 element index (那些数字) 是**当次 DOM 快照下的**指针, 一旦页面动 (新内容加载 / scroll / 弹层) 全部失效.

- **每次操作前重新 `state`** 拿最新 index, 不要相信几步之前的 index
- 工具会在 state 输出前 prepend STOP 警告, 看到要认真处理
- 想"记住"一个元素的标识, 走 `get text <index>` / `eval` 拿到稳定属性 (文本 / id / class / data-*) 再用 CSS selector 定位 — 把"瞬态 index"升级成"稳定 selector"

### 铁律 5: 下载工作流 — 不要盲点下载按钮

页面上有"下载"按钮不代表点了就能保存到本地. browser-use CLI 没有透明的下载落盘机制.

正确做法:

1. 先 `state` 确认按钮是真下载链接 (有 `href` 直链) 还是 JS 触发的
2. **如果是直链 `<a href="...">`**: 直接 `eval "return document.querySelector('selector').href"` 拿 URL → 用 Python 脚本 (`run_python_script` + `httpx`) 下载, 走 sandbox 路径落盘
3. **如果是 JS 触发**: 用 `eval` 注入 fetch / XHR / blob hook 抓真实下载源, 再走方式 2
4. 不要直接点按钮然后假设"下载好了" — sandbox 里看不到的文件等于没有

抓完用 `Read` 工具验证文件存在 + 大小合理才能算下载完成.

## 元素定位

### 索引 → CSS selector 升级路径

`state` 给的 index 适合**一次性**操作 (click N / type N text). 任何"要复用"或"要写到脚本里"的定位必须升级:

**L1 索引** (一次性, 不可复用)
```
browser_use(command='click 7')
```

**L2 文字定位** (跨刷新有效, 但同名重复时失败)
```
browser_use(command='eval "document.querySelector(\\"button[aria-label=\\\\\"提交\\\\\"]\\").click()"')
```

**L3 稳定 selector** (推荐, 适合脚本)
- 找元素的 id / data-* / 唯一 class
- `eval` 里 `document.querySelector("#main-submit").click()` — 跨会话稳

**反模式**:
- ❌ 长 XPath: `/html/body/div[3]/div[2]/...` (DOM 一动就废)
- ❌ nth-child: `div:nth-child(5)` (动态列表会错位)
- ❌ 把 state 里的 index 硬编码进脚本

### 给生成脚本用的元素定位

如果用户要"把这个流程生成一个可复用 Python 脚本", 必须先用 L2 / L3 把所有 click / type 的目标定位升级成稳定 selector. 流程:

1. 探索阶段用 L1 (索引) 快速验证流程跑得通
2. 流程稳定后, 每个操作点重新拿: `eval` 跑 `outerHTML` / `getAttribute` 抓特征, 找最稳的 selector
3. 写脚本时用 `mcp__pentaloom_browser__browser_use_session_info()` 拿 session_name / profile / cookies_path 当常量

## 数据提取策略

按"数据在哪"分支:

| 数据位置 | 取法 |
|---|---|
| 单个元素的文本 | `get text <index>` (state 后立刻用, index 还有效) |
| 多元素 / 列表 | `eval "return Array.from(document.querySelectorAll('.item')).map(e => e.textContent)"` |
| 元素属性 (href / src / data-*) | `eval "return document.querySelector('selector').getAttribute('href')"` |
| 整页结构化数据 | 先 `state` 找定位锚, 再 `eval` 走 selector 抽 |
| 图片 / canvas | `screenshot foo.png` (相对路径自动落到 sandbox), 再用 file_read / Read 看 |
| 网络请求体 (XHR 返回) | `eval` 注入 fetch 钩子, 把响应缓存到全局变量, 下一步 `eval` 读出来 |

**禁忌**: 不要把 `document.body.innerText` 整个塞给用户当答案 — 太长、混入导航/广告/footer、噪音大. 一定要 scoped 抽.

## 失败处理

按错误类型对应措施:

| 现象 | 措施 |
|---|---|
| `browser_use` 报 "CLI 未安装" | 先调 `install_browser_use(step='check')` 看哪步缺, 按 next_step 装 |
| `browser_use` 报 "未检测到浏览器" | 调 `install_browser_use(step='chromium')` |
| 命令 exit 非 0 | 看 output 里 browser-use 自己的报错, 按错误调整命令, 别盲重试 |
| `state` 输出空 / 报 "no active session" | session 可能掉了, 重新 `open URL` |
| `click N` 报索引越界 | 页面变了, 重 `state` 拿新 index |
| 操作没生效 (页面无变化, URL 没变) | 走铁律 3 检查标签页; 仍无果, 提示用户手动接手 |
| 超时 (5 分钟 open / 2 分钟其它) | 页面卡死或网络坏; 给用户报告环境问题, 不要傻重试 |

## 工具速查

| 工具 | 用途 |
|---|---|
| `mcp__pentaloom_browser__install_browser_use(step)` | 装环境, step ∈ check / install / chromium |
| `mcp__pentaloom_browser__browser_use(command)` | 跑一条 CLI 子命令, command 跟 browser-use CLI 一致 |
| `mcp__pentaloom_browser__browser_use_session_info()` | 拿 session_name / profile / cookies_path (生成脚本时用) |

常用 CLI 命令体 (传给 `command`):
- `open URL` / `open URL --headed` / `--profile NAME open URL`
- `state`
- `click N` / `dblclick N` / `rightclick N` / `hover N`
- `type N "text"` / `input N "text"` / `keys "Enter"`
- `select N "option"`
- `scroll up|down|<px>`
- `eval "JS code"` (复杂 JS 工具自动 quote)
- `extract <selector>` / `get text N` / `get attributes N`
- `screenshot [path]` / `screenshot --full [path]` (相对路径自动落 sandbox)
- `switch N` / `back` / `close-tab`
- `cookies export|import <path>` (一般不用手动调, close/open 自动)
- `close`

## 脚本化模式 (生成可复用 Python 脚本)

适用于用户明确说"把这个流程写成脚本以后我自己跑" / "定时任务" / "做成命令行工具"等场景. **一次性任务不用进这个模式**, 直接交互完成 + 报告结果就行.

### 触发条件 (任一)

- 用户明示要"脚本" / "可复用" / "定时" / "命令行"
- 同一个流程要在多个 input 上重复跑 (比如批量抓 100 个股票代码)
- 用户提到"以后" / "下次" / "周期"等暗示要重复使用

### 准备工作 (进脚本化前必须做完)

1. **交互模式把整条流程跑通一遍**. 没跑通不要写脚本.
2. **把所有 click / type / get 的目标定位升级到 stable selector** (id / data-* / aria-*).
   见前面 "元素定位" 一节. 反模式: 把 state 给的 index 数字硬编码进脚本.
3. **调 `mcp__pentaloom_browser__browser_use_session_info()`** 拿这四个常量, 待会写死进脚本:
   - `session_name` (= 当前 PentaLoom 会话对应的 browser-use session 名)
   - `profile` (可能是 null)
   - `cookies_path` (cookies 自动导出的位置)
   - `python_env` 路径 — 这个不在 session_info 里, 从用户说过的 sandbox 路径反推
     `<sandbox-parent>/python-env`, 或者直接 `pwd` 给 `Bash` 看看

### 模板路径

`.claude/skills/browser-use/script_template.py` — **必须从这个模板开始改**, 不要从零写, 不要换成 Playwright / Selenium / pyppeteer.

用 `Read` 工具读模板, 改四个常量 + 主流程, 然后 `Write` 到 sandbox 里.

### 强制纪律

| 项 | 必须 | 禁止 |
|---|---|---|
| 元素定位 | stable selector (id / data-* / aria-*) | 硬编码 state 的瞬态 index |
| 失败处理 | 立刻 raise / exit 非 0 | 静默 try-except 吞掉 |
| 浏览器复用 | 用模板的 `bu()` helper, 自动注入 session 名 | 自己拼 subprocess 命令绕过 helper |
| cookies | 启动调一次 `ensure_cookies()` 自动恢复登录 | 让用户每次手动登 |
| 关闭 | 默认留浏览器 | 主动 `close` 除非用户要求 |
| 自验证 | `run_python_script` 跑一次, 异常 0 才算交付 | 写完直接给用户路径 |

### 强制自执行验证

写完脚本必须立刻调 `mcp__pentaloom_env__run_python_script(script_path=...)` 跑一遍 (用户授权):

- exit 0 且核心输出 (print 出来的关键数据 / 文件存在) 看着对 → 才算交付
- exit 非 0 或者输出明显错 → 走下面 recovery, 不要直接说"脚本写好了"

## 脚本失败 recovery

**铁律**: 看到 traceback / stderr **不要盲改代码瞎试**. 按错误分类回路:

| stderr 关键词 | 大概率原因 | 回路 |
|---|---|---|
| `selector not found` / `NoneType ... has no attribute` | DOM 变了 / selector 写错 | 回交互模式 `browser_use(open)` + `state`, 对照实际 DOM 重新 `eval` 拿新 selector, 改脚本 |
| `Timeout` / `wait timed out` | 网络慢 / 页面没加载完 | 改 `wait`/`sleep` 时长, 或者 `eval` 等关键元素出现 |
| `cookies` 相关错 | cookies 文件不存在 / 已失效 | 回交互模式重新登录一次, 让工具自动重新导出 cookies, 再跑脚本 |
| `browser-use: command not found` / `uv: command not found` | PYTHON_ENV 路径写错 | 调 `Bash` 跑 `ls $PYTHON_ENV/.venv/bin/browser-use` 确认路径 |
| reCAPTCHA / 登录页 | 真实人机验证或登录态彻底丢 | **中断脚本化**, 把状况告诉用户, 让他手动过验证, 通过后再重试 |

修一处跑一次, 别一次改一堆 — 否则定位变量多了说不清是哪改起的效.

如果同一个错误改三次还不过, 别死磕脚本路径, 退回交互模式问用户: 是不是需求本身不对.

## 给用户看的总结

任务完成时简要报告:
- 做了什么 (登录了哪个站 / 抓到多少条数据 / 截了几张图)
- 文件落在哪 (sandbox 里的绝对路径)
- 浏览器有没有关 (留着或已 close)
- 还需要用户做什么 (有时候要确认下载 / 校验抓到的数据)

不要把 raw state / DOM 摘要原样贴给用户.
