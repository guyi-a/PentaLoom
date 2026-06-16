// loomer testdata — 多文件 React app, 用来端到端验证 esbuild + webview + IPC.
//
// 跑法:
//   cd loomer && go build -o /tmp/loomer . && /tmp/loomer --entry testdata/hello-app/index.tsx
//
// 期望:
//   - 弹窗 600×400, 标题 "index" (entry 文件名 fallback)
//   - 显示 "Hello from PentaLoom loomer" 标题
//   - Card 组件显示 (验证多文件 import + Tailwind-ish style)
//   - 按钮触发 window.invokeApp({tool:"ping",args:{...}}) → JS 收到 echo 后显示
import { useState } from "react";
import { Card } from "./Card";
import { fmtKB } from "./helpers";

export const windowConfig = {
  title: "loomer hello",
  width: 600,
  height: 400,
};

export default function App() {
  const [echo, setEcho] = useState<string | null>(null);
  const [count, setCount] = useState(0);

  async function ping() {
    // window.invokeApp 由 loomer ui.go Bind 注入, 见 ui.Run 里的 w.Bind("invokeApp", ...)
    // PR 1 默认 echo, PR 2 接 loom daemon
    // @ts-expect-error global injected by loomer
    const r = await window.invokeApp({ tool: "ping", args: { count } });
    setEcho(JSON.stringify(r, null, 2));
    setCount((c) => c + 1);
  }

  return (
    <div style={{ padding: 32, maxWidth: 540, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>
        Hello from PentaLoom loomer
      </h1>
      <Card title={`bundle size demo: ${fmtKB(12_345)}`}>
        <p style={{ color: "#666", fontSize: 13, lineHeight: 1.5 }}>
          这个窗由 loomer 渲: esbuild 把 index.tsx + Card.tsx + helpers.ts 打成
          一段 ESM JS, importmap 让 React 走 esm.sh. 点按钮触发双向 IPC.
        </p>
        <button
          onClick={ping}
          style={{
            marginTop: 12,
            padding: "8px 16px",
            border: "1px solid #ccc",
            borderRadius: 6,
            background: "white",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          调 invokeApp (count: {count})
        </button>
      </Card>
      {echo && (
        <pre
          style={{
            marginTop: 16,
            background: "#f0f0f0",
            padding: 12,
            borderRadius: 6,
            fontSize: 12,
            whiteSpace: "pre-wrap",
          }}
        >
          {echo}
        </pre>
      )}
    </div>
  );
}
