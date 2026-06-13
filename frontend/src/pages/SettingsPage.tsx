/** SettingsPage — 浏览器连接 / 主题 / 版本.
 *
 * 浏览器连接状态每 5 秒轮询.
 * 主题切换从 props 接收, 不走 Outlet context.
 */

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";

import { api } from "@/lib/api";
import type { ConnectionStatus } from "@/lib/types";
import type { Theme } from "@/lib/theme";
import {
  SectionHeader,
  SettingRow,
  SettingSelect,
} from "@/components/settings/_shared";
import { EmailDialog } from "@/components/settings/EmailDialog";

const BROWSER_EXT_URL =
  "https://chromewebstore.google.com/detail/kro-browser-bridge/ggnaffooacplgigkdjgmakggbbhjdcfj";

interface SettingsPageProps {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

export function SettingsPage({ theme, setTheme }: SettingsPageProps) {
  // ── settings (version) ──
  const { data: cfg } = useSWR("settings", api.getSettings);

  // ── connections (5s polling) ──
  const [conn, setConn] = useState<ConnectionStatus | null>(null);
  const [connError, setConnError] = useState(false);

  // ── email dialog ──
  const [emailDialogOpen, setEmailDialogOpen] = useState(false);

  const loadConn = useCallback((silent = false) => {
    api
      .getConnections()
      .then(setConn)
      .catch(() => {
        if (!silent) setConn(null);
        setConnError(true);
      });
  }, []);

  useEffect(() => {
    loadConn(true);
    const id = window.setInterval(() => loadConn(true), 5000);
    return () => window.clearInterval(id);
  }, [loadConn]);

  return (
    <div className="min-w-0 flex-1 overflow-y-auto bg-[var(--color-bg)]">
      <div className="max-w-[600px] mx-auto px-8 pt-8 pb-16 space-y-10">
        {/* header */}
        <div className="flex items-center">
          <h2 className="text-lg font-semibold tracking-tight text-[var(--color-fg)]">
            设置
          </h2>
        </div>

        {/* Connections */}
        <section>
          <SectionHeader label="连接" />
          <div className="space-y-3">
            <SettingRow
              label="浏览器扩展"
              hint={
                conn?.browser_bridge_ready
                  ? `已连接 ${conn.browser_bridge_browsers} 个浏览器`
                  : connError
                    ? "检查连接失败"
                    : "未连接"
              }
            >
              <div className="flex items-center gap-2">
                <span
                  className={`inline-block w-2 h-2 rounded-full ${
                    conn?.browser_bridge_ready ? "bg-green-500" : "bg-gray-400"
                  }`}
                />
                <span className="text-xs text-[var(--color-fg-dim)]">
                  {conn?.browser_bridge_ready ? "已连接" : "离线"}
                </span>
              </div>
            </SettingRow>
            {!conn?.browser_bridge_ready && (
              <div className="pl-4">
                <a
                  href={BROWSER_EXT_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-[var(--color-accent)] hover:underline"
                >
                  安装 Kro Browser Bridge 扩展 →
                </a>
              </div>
            )}
            {conn?.browser_bridge_ready &&
              conn.browser_bridge_detail.length > 0 && (
                <div className="pl-4 space-y-1">
                  {conn.browser_bridge_detail.map((b) => (
                    <div
                      key={b.browser_id}
                      className="text-xs text-[var(--color-fg-dim)]"
                    >
                      {b.label}
                    </div>
                  ))}
                </div>
              )}

            {/* Email */}
            <SettingRow
              label="邮箱"
              hint={
                conn?.email_connected
                  ? conn.email_account ?? "已配置"
                  : "未配置"
              }
            >
              <div className="flex items-center gap-2">
                <span
                  className={`inline-block w-2 h-2 rounded-full ${
                    conn?.email_connected ? "bg-green-500" : "bg-gray-400"
                  }`}
                />
                <span className="text-xs text-[var(--color-fg-dim)]">
                  {conn?.email_connected ? "已连接" : "离线"}
                </span>
                <button
                  onClick={() => setEmailDialogOpen(true)}
                  className="text-[11px] text-[var(--color-accent)] hover:underline"
                >
                  {conn?.email_connected ? "配置" : "添加"}
                </button>
              </div>
            </SettingRow>
          </div>
        </section>

        {/* Appearance */}
        <section>
          <SectionHeader label="外观" />
          <SettingRow label="主题" hint="选择界面颜色主题">
            <SettingSelect
              value={theme}
              onChange={(v) => setTheme(v as Theme)}
              options={[
                { value: "light", label: "浅色" },
                { value: "dark", label: "深色" },
                { value: "system", label: "跟随系统" },
              ]}
            />
          </SettingRow>
        </section>

        {/* About */}
        <section>
          <SectionHeader label="关于" />
          <SettingRow label="版本">
            <span className="text-sm text-[var(--color-fg-dim)]">
              {cfg?.version ?? "—"}
            </span>
          </SettingRow>
        </section>
      </div>

      <EmailDialog
        open={emailDialogOpen}
        onClose={() => setEmailDialogOpen(false)}
        onSaved={() => loadConn(true)}
      />
    </div>
  );
}
