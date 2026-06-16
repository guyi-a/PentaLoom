import type { ReactNode } from "react";

export function Card({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid #e5e5e5",
        borderRadius: 8,
        padding: 16,
        background: "#fafafa",
      }}
    >
      <h2 style={{ fontSize: 14, fontWeight: 600, marginTop: 0, marginBottom: 8 }}>
        {title}
      </h2>
      {children}
    </div>
  );
}
