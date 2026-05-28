---
name: browser-bridge
description: "用户真实浏览器自动化任务 (Chrome + Kro Browser Bridge 扩展). 触发场景: 用户说\"打开网站\" / \"帮我登录\" / \"截图\" / \"提交表单\" / \"抓这个页面\" / \"在 X 网站上做 Y\" 等, 且 extension_status 返回 ready=true. 用真实浏览器, 自动保留所有登录态. 比 browser-use CLI 快、稳、贴近用户. 包含强制工作流 (extension_status → list_pages / open_tab → read_state → 用 index 操作 → 验证) + 五条铁律 + DOM 优先 + describe_element 升 selector 路径. 不适用: 扩展未连接 (走 browser-use skill 降级) / 纯命令行任务."
---

# Skill: browser-bridge (用户真实浏览器自动化)

适用场景: 用户要在浏览器里完成事情 — 登录 / 检索 / 抓取 / 截图 / 填表 / 验证 web 页面交互, **且 `extension_status` 返回 ready=true**.

不适用: 扩展未装 / 未连接 → 切到 `browser-use` skill 走 CLI 兜底.

## 工作流 (MUST 顺序)

1. **决策路径**: 任何浏览器任务先调
   `mcp__pentaloom_browser_bridge__browser_bridge(action="extension_status")`:
   - `ready=true` → 走本 skill (bridge)
   - `ready=false` → load `browser-use` skill, 走 CLI 路径; 不在这里硬扛
2. **找浏览器 + 标签**:
   - `extension_status` 返回里有 `browser_ids` — 拿第一个 (用户通常只有一个 Chrome)
   - 调 `browser_bridge(action="list_pages", browser_id=...)` 看用户当前已开的所有 tab. 任务可能不需要新开页面, 复用现有 tab 更友好.
3. **打开 / 切换页面**:
   - 需要新页 → `open_tab(browser_id, url)`
   - 用户已有相关 tab → `focus_page(browser_id, page_id)` 切过去
4. **看页面状态**: `read_state(browser_id, page_id)` 拿 markdown — 含元素 index + 关键文本. 这是后续 click / type 的"地图".
5. **执行动作**: 按 read_state 给的 index 调 `click N` / `type N "text"` / `press key` / `scroll` / `select_dropdown` 等.
6. **验证完成**: 操作后再 `read_state` 一次, 用 URL 变化 / 关键元素出现判断动作真生效 (见铁律 1).
7. **收尾**: **默认不关 tab**, 让用户能继续看. 只有用户明确说"关掉"才 `close_tab`. 浏览器本身始终留着 — bridge 不掌控用户的 Chrome.

## 五条铁律

### 铁律 1: 操作成功 ≠ 任务成功

`click` 返回 `navigates=true` 只表示页面发起了导航, 不代表业务真完成. `type` 只表示文字注入了 DOM, 不代表表单 submit. 关键动作后必须再 `read_state` 用以下任一确认:

1. URL 变成预期下游页 (登录页 → dashboard 等)
2. 页面出现明确成功标志 (订单号 / 用户头像 / "成功"文字)
3. 明显失败信号 (红框错误 / 弹窗)

证据不齐不能向用户报"已完成".

### 铁律 2: 用户阻断 = 立刻终止

reCAPTCHA / 短信 2FA / 银行支付 / 实名验证 / 法律条款勾选 / 用户自己的密码框 → **立刻停**, 把状态告诉用户, 等他手动处理完再继续. 别凭空猜密码 / 替用户点同意.

bridge 比 browser-use 更要小心 — 这是用户**真实浏览器**, 误操作影响真账号.

### 铁律 3: URL 未变 + 页面未变 → 检查标签页

某些链接点了会新开 tab, 当前 tab 不变. 看到 click 后 read_state 内容没变, 别立刻重试:
- `list_pages(browser_id)` 看 tab 总数
- 找新出现的 tab (last_seen_at 最新), `focus_page` 切过去再 read_state
- 用户原本就开了 10 个 tab 也很常见, 别假设"只有我开的那个"

### 铁律 4: index 是瞬态证据, 不准存活到下次操作

`read_state` 给的 element index 是**当次 DOM 快照下的**指针, 一旦页面动 (scroll / 弹层 / 新内容 / SPA 路由) 全部失效.

- **每次操作前重新 `read_state`** 拿最新 index
- 想跨多次操作记一个元素, 走 `describe_element(index)` 拿稳定 selector — 见下面"selector 升级路径"
- 同 verb 多次失败, 90% 是 index 过期, 别迷信"重试同 index"

### 铁律 5: DOM 优先 (跟 browser-use 不一样的最大点)

bridge 跑在用户真实浏览器, DOM 是最稳的数据来源. **能从 DOM 拿到的, 不要走网络 / 截图 OCR / 模糊匹配**:

| 任务 | 优先 | 次选 | 反模式 |
|---|---|---|---|
| 抓页面文字 | `read_state` 的 markdown / `extract(index)` | `execute_script(return ...)` | OCR 截图 |
| 抓链接 / 属性 | `describe_element(index)` → href / attributes | `execute_script` | 截图 + 视觉解析 |
| 抓列表数据 | `execute_script("return Array.from(...)")` | `read_state` 解析 markdown | 多次 click 累积 |
| 抓动态内容 | 等元素出现后 `extract` / `execute_script` | 反复 read_state 等 | 截图差分 |

理由: bridge 跑在用户机器, DOM 直接拿就是, 不需要拐弯. 网络监控 (v1 砍掉) / 截图 OCR 都是没办法时的兜底, **优先级始终是 DOM**.

## selector 升级路径 (bridge 独有, 比 browser-use 强)

read_state 给的 index 是瞬态. 想要稳定、跨刷新可用的定位, 调:

```
browser_bridge(action="describe_element", browser_id=..., page_id=..., index=N)
```

返回 `selector` (CSS) + `selector_matches` (整页有多少元素匹配这个 selector). 用法:

- `selector_matches == 1` → 这个 selector **唯一**, 可以用. 写脚本时拷贝下来.
- `selector_matches > 1` → 不唯一, **不要直接用**, 否则脚本里会点错元素. 改用别的特征 (id / data-* / 加 parent selector 收紧 scope) 或换 `execute_script` 写更具体的 querySelector.
- 没有 `selector` 字段 (元素太通用没 stable 特征) → 退回用 index, 接受瞬态局限.

跟 browser-use 对比:
- browser-use: 只有 index, 想升 selector 要自己 `eval document.querySelector(...)` 试错
- bridge: 一个工具 (`describe_element`) 直接给 selector + 唯一性校验

## 数据提取策略

按"数据位置"分支:

| 数据位置 | 走法 |
|---|---|
| read_state 已经给出文本 | 直接用, 别多调 |
| 单个具体元素 | `extract(index)` 拿结构化字段 (text / href / attributes) |
| 列表 (多个相似元素) | `execute_script("return Array.from(document.querySelectorAll('.item')).map(...)")` |
| 跨 frame / shadow DOM | `execute_script` 里手写穿透 (注意 shadow root 的 mode='open' 才能访问) |
| 网络请求体 (XHR 返回) | v1 砍掉 network_log; 临时用 `execute_script` 注入 fetch 钩子, 后续重做 |
| 下载文件 | v1 砍掉自动下载, 暂时让用户手动下到本地; 后续版本会接 |

## 失败处理

| 现象 | 措施 |
|---|---|
| `extension_status` ready=false | 切 `browser-use` skill (CLI 兜底), 别在这里耗 |
| `browser_id not connected` / `browser websocket not available` | 扩展掉线; 先 `extension_status` 看一眼, 提示用户检查 Chrome 是否打开 + 扩展是否启用 |
| `page_id not found` | tab 关了 / 重启过; 调 `list_pages` 拿新 page_id |
| `504 Timeout` | 页面卡死; 先 read_state, 看是不是弹层挡住了 / 长 handler 跑着 |
| index 类操作 (click/type/...) 报错 | index 过期; 重 read_state 拿新 index |
| reCAPTCHA / 登录页 | 走铁律 2, 中断给用户 |

## 跟 browser-use 的差异速查

| 维度 | browser-bridge | browser-use |
|---|---|---|
| 浏览器 | 用户真实 Chrome | 独立机器人 Chrome (Playwright Chromium) |
| 登录态 | 自动保留 (用户登过的) | 空, 要靠 cookies 导入导出续命 |
| 元素定位 | index → `describe_element` 升 selector | index → `eval document.querySelector` 自己试 |
| 速度 | 快 (WebSocket + Chrome Ext API) | 慢 (每次 spawn CLI 子进程) |
| 用户可见性 | 用户看着你点 | 用户看着 PentaLoom 起的另一个 Chrome 弹出来 |
| 下载 | v1 砍, 用户自己存 | v1 砍, 同 |
| 适用场景 | 用户日常浏览器里的事 | 用户没装扩展时的兜底 |

## 工具速查

只有一个工具, 用 action 分发:

```
browser_bridge(action=..., 其它参数按 action 需要传)
```

action 全集:
- 决策: `extension_status`
- 查询: `list_sessions` / `list_pages(browser_id)` / `list_windows(browser_id)`
- 导航: `open_tab(browser_id, url)` / `focus_page(browser_id, page_id)` / `close_tab(browser_id, page_id)` / `reload(browser_id, page_id)` / `go_back(browser_id, page_id)`
- 观察: `read_state(browser_id, page_id)` / `wait_for(browser_id, page_id, timeout_ms)` / `describe_element(browser_id, page_id, index)`
- 交互: `click` / `hover` / `dblclick` / `rightclick` (都要 index)
- 输入: `type(browser_id, page_id, index, text)` / `press(browser_id, page_id, key, [index])`
- 滚动: `scroll(browser_id, page_id, x, y, [index])`
- 抽取: `extract(browser_id, page_id, index)` / `dropdown_options(browser_id, page_id, index)` / `select_dropdown(browser_id, page_id, index, text)`
- 高级: `execute_script(browser_id, page_id, script)` — script 必须 `return`, 支持 async / await

## 给用户看的总结

任务完成时简要报告:
- 做了什么 (打开了哪些站 / 抓到多少条数据 / 完成了哪步操作)
- 浏览器里现在能看到什么 (新 tab 在第几个 / 关键元素在哪)
- 还需要用户做什么 (有时候要确认 / 校验 / 手动收尾)

不要把 raw read_state markdown 原样贴给用户.
