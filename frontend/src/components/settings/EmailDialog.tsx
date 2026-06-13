/** EmailDialog — 邮箱账号配置弹窗.
 *
 * 支持 Gmail / QQ 邮箱添加, SMTP 验证后存盘.
 * 已配置时显示当前账号 + 删除 + 发测试邮件.
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type {
  AddEmailAccountBody,
  EmailAccountResponse,
  EmailProviderInfo,
} from "@/lib/types";
import { SettingInput, SettingSelect } from "./_shared";

interface EmailDialogProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

type DialogStep = "idle" | "adding" | "validating" | "done" | "error";

export function EmailDialog({ open, onClose, onSaved }: EmailDialogProps) {
  const [providers, setProviders] = useState<EmailProviderInfo[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountResponse[]>([]);

  // form
  const [provider, setProvider] = useState("gmail");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");

  // state
  const [step, setStep] = useState<DialogStep>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState("");

  // load providers + accounts
  useEffect(() => {
    if (!open) return;
    api.getEmailProviders().then((r) => setProviders(r.providers));
    api.getEmailAccounts().then((r) => {
      setAccounts(r.accounts);
    });
  }, [open]);

  const handleAdd = useCallback(async () => {
    setErrorMsg("");
    setStep("validating");
    try {
      const body: AddEmailAccountBody = {
        provider,
        email,
        password,
        display_name: displayName || undefined,
      };
      const result = await api.addEmailAccount(body);
      if (result.ok) {
        setStep("done");
        onSaved();
        // refresh accounts
        const r = await api.getEmailAccounts();
        setAccounts(r.accounts);
        // reset form
        setEmail("");
        setPassword("");
        setDisplayName("");
      } else {
        setStep("error");
        setErrorMsg(result.message);
      }
    } catch (e: any) {
      setStep("error");
      setErrorMsg(e?.message || "添加失败");
    }
  }, [provider, email, password, displayName, onSaved]);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await api.deleteEmailAccount(id);
        onSaved();
        const r = await api.getEmailAccounts();
        setAccounts(r.accounts);
      } catch {
        /* ignore */
      }
    },
    [onSaved],
  );

  const handleTest = useCallback(async (id: string) => {
    setTestingId(id);
    setTestMsg("");
    try {
      const result = await api.testEmailAccount(id);
      setTestMsg(result.ok ? "测试邮件已发送" : `失败: ${result.message}`);
    } catch {
      setTestMsg("请求失败");
    }
    setTestingId(null);
  }, []);

  if (!open) return null;

  const preset = providers.find((p) => p.id === provider);
  const emailSuffix = preset?.email_suffix ?? "";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-[420px] max-h-[85vh] overflow-y-auto rounded-xl bg-[var(--color-bg-card)] border border-[var(--color-line)] shadow-xl"
        role="dialog"
        aria-label="邮箱配置"
      >
        {/* header */}
        <div className="flex items-center justify-between border-b border-[var(--color-line)] px-5 py-4">
          <h3 className="text-sm font-semibold text-[var(--color-paper)]">
            邮箱配置
          </h3>
          <button
            onClick={onClose}
            className="text-[var(--color-ink-dim)] hover:text-[var(--color-paper)] transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* body */}
        <div className="px-5 py-4 space-y-4">
          {/* 已配置的账号 */}
          {accounts.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-medium text-[var(--color-ink)]">
                已配置
              </div>
              {accounts.map((acc) => (
                <div
                  key={acc.id}
                  className="flex items-center gap-3 rounded-lg bg-[var(--color-bg-deep)] px-3 py-2"
                >
                  <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] text-[var(--color-paper)]">
                      {acc.email}
                    </div>
                    <div className="text-[11px] text-[var(--color-ink-dim)]">
                      {providers.find((p) => p.id === acc.provider)?.display_name ?? acc.provider}
                      {acc.is_default && " · 默认"}
                    </div>
                  </div>
                  <button
                    onClick={() => handleTest(acc.id)}
                    disabled={testingId === acc.id}
                    className="text-[11px] text-[var(--color-accent)] hover:underline disabled:opacity-50"
                  >
                    {testingId === acc.id ? "发送中..." : "测试"}
                  </button>
                  <button
                    onClick={() => handleDelete(acc.id)}
                    className="text-[11px] text-[var(--color-error)] hover:underline"
                  >
                    删除
                  </button>
                </div>
              ))}
              {testMsg && (
                <div className="text-[11px] text-[var(--color-ink)]">{testMsg}</div>
              )}
            </div>
          )}

          {/* 分割线 */}
          {accounts.length > 0 && (
            <div className="border-t border-[var(--color-line-soft)]" />
          )}

          {/* 添加表单 */}
          <div className="space-y-3">
            <div className="text-xs font-medium text-[var(--color-ink)]">
              {accounts.length > 0 ? "添加新账号" : "添加邮箱账号"}
            </div>

            <div className="space-y-2">
              <label className="text-[11.5px] text-[var(--color-ink-dim)]">
                邮箱服务商
              </label>
              <SettingSelect
                value={provider}
                onChange={setProvider}
                options={providers.map((p) => ({
                  value: p.id,
                  label: p.display_name,
                }))}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[11.5px] text-[var(--color-ink-dim)]">
                邮箱地址
              </label>
              <SettingInput
                value={email}
                onChange={(v) => setEmail(v)}
                placeholder={emailSuffix ? `yourname${emailSuffix}` : "your@email.com"}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[11.5px] text-[var(--color-ink-dim)]">
                {provider === "qq" ? "授权码" : "应用专用密码"}
              </label>
              <SettingInput
                value={password}
                onChange={(v) => setPassword(v)}
                type="password"
                placeholder={provider === "qq" ? "QQ邮箱授权码" : "App password"}
                monospace
              />
            </div>

            {/* QQ 提示 */}
            {provider === "qq" && (
              <div className="text-[11px] text-[var(--color-warn)] bg-[var(--color-bg-deep)] rounded-lg px-3 py-2">
                QQ 邮箱请使用授权码而非 QQ 密码。在 QQ 邮箱设置 → 账户 → POP3/IMAP/SMTP 里生成授权码。
              </div>
            )}

            {/* Gmail 提示 */}
            {provider === "gmail" && (
              <div className="text-[11px] text-[var(--color-ink)] bg-[var(--color-bg-deep)] rounded-lg px-3 py-2">
                Gmail 需要开启两步验证后生成应用专用密码。前往 Google 账号 → 安全性 → 应用密码。
              </div>
            )}

            <div className="space-y-2">
              <label className="text-[11.5px] text-[var(--color-ink-dim)]">
                发件人名称（可选）
              </label>
              <SettingInput
                value={displayName}
                onChange={(v) => setDisplayName(v)}
                placeholder="显示名称"
              />
            </div>
          </div>

          {/* error */}
          {step === "error" && errorMsg && (
            <div className="text-[11px] text-[var(--color-error)] bg-[var(--color-bg-deep)] rounded-lg px-3 py-2">
              {errorMsg}
            </div>
          )}
        </div>

        {/* footer */}
        <div className="flex items-center justify-end gap-2 border-t border-[var(--color-line)] px-5 py-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-1.5 text-[12.5px] text-[var(--color-ink)] hover:bg-[var(--color-bg-deep)] transition-colors"
          >
            关闭
          </button>
          <button
            onClick={handleAdd}
            disabled={step === "validating" || !email || !password}
            className="rounded-lg bg-[var(--color-accent)] px-4 py-1.5 text-[12.5px] font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-40"
          >
            {step === "validating" ? "验证中..." : "验证并添加"}
          </button>
        </div>
      </div>
    </div>
  );
}
