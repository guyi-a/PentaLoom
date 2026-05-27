// 五瓣线徽标 — 用 SVG 画五根从同一原点出发的丝线, 交织成五瓣花形.
// 五根线分别对应 file / app_gen / browser / computer / search agent 的色.
// active 状态时缓慢"拉伸", idle 时静止 (但仍有微微暖光).

import { cn } from "@/lib/utils";

interface Props {
  size?: number;
  active?: boolean;
  className?: string;
}

const THREADS = [
  { color: "var(--color-thread-file)", angle: -90 },
  { color: "var(--color-thread-app)", angle: -18 },
  { color: "var(--color-thread-browser)", angle: 54 },
  { color: "var(--color-thread-computer)", angle: 126 },
  { color: "var(--color-thread-search)", angle: 198 },
];

export function LoomMark({ size = 40, active = false, className }: Props) {
  const r = size / 2 - 2;
  const cx = size / 2;
  const cy = size / 2;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={cn("block", className)}
    >
      {/* 五瓣 — 每瓣是从中心向外的细长椭圆, 旋转排列 */}
      <g style={{ transformOrigin: `${cx}px ${cy}px` }}>
        {THREADS.map((t, i) => {
          const rad = (t.angle * Math.PI) / 180;
          const x2 = cx + Math.cos(rad) * r;
          const y2 = cy + Math.sin(rad) * r;
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={x2}
              y2={y2}
              stroke={t.color}
              strokeWidth={1.4}
              strokeLinecap="round"
              className={active ? "thread-pulse" : ""}
              style={{ animationDelay: `${i * 0.18}s` }}
            />
          );
        })}
      </g>
      {/* 中心结 */}
      <circle
        cx={cx}
        cy={cy}
        r={1.8}
        fill="var(--color-paper)"
        opacity={0.85}
      />
      {/* 外圈细线 */}
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke="var(--color-line-strong)"
        strokeWidth={0.5}
        opacity={0.5}
      />
    </svg>
  );
}
