import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  connectGoogleGmail,
  disconnectGmailAccount,
  setGmailDefaultAccount,
  setGmailFilters,
  type GmailAccount,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { ConnectSetup } from "../ManageTabs";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, TAG_WARN, XBTN } from "./ui";

// The Gmail detail page (UX-DECISIONS §21): connected mailboxes (multi-account,
// Default badge, per-account disconnect) + "Never show agents" privacy filters.
// Adding an account launches Astra-owned Google OAuth directly — Gmail has one connect mode,
// so no modal (the pill-modal is only for ≥2-mode connectors like Slack).

const LABEL = "text-[13px] text-muted w-24 shrink-0";

export function GmailDetail({ c, cloud, slack: _slack, onChanged }: DetailProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accounts = (c.accounts ?? []) as GmailAccount[]; // email-keyed (pre-generic-layer shape)
  const canNativeOAuth = !!c.native_oauth;

  const addAccount = async () => {
    setBusy(true);
    setError(null);
    const result = await connectGoogleGmail();
    if (!result.ok) {
      setError(result.error || t("gmail.oauth_start_failed"));
      setBusy(false);
      return;
    }
    setTimeout(() => setBusy(false), 2500);
  };

  return (
    <div data-testid="gmail-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="Gmail" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">Gmail</h2>
          <div className="text-[13px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="gmail-status">
                  {t("connector.account_count", { count: accounts.length })}
                </span>
              </>
            ) : (
              <span>{t("connector.not_connected")}</span>
            )}
          </div>
        </div>
        <button
          className={PILL_ACCENT + (c.managed_paused ? " opacity-50" : "")}
          data-testid="add-account-btn"
          onClick={() => (canNativeOAuth ? addAccount() : setShowManual((v) => !v))}
          disabled={busy || (c.managed && !!c.managed_paused)}
        >
          {c.managed_paused
            ? t("gmail.add_account_coming_soon")
            : busy
              ? t("cloud.check_browser")
              : t("gmail.add_account")}
        </button>
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[13px] text-muted"}>
            {t("gmail.setup_blurb")}
            {!canNativeOAuth && " " + t("gmail.oauth_not_configured")}
          </div>
          {error && <div className="px-3.5 pb-3 text-[13px] text-danger">{error}</div>}
        </div>
      )}

      {accounts.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{t("gmail.accounts")}</div>
          <div className={GRP} data-testid="gmail-accounts">
            {accounts.map((a) => (
              <AccountRow key={a.email} a={a} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      {!c.managed && showManual && (
        <>
          <div className={GRP_H}>{t("accounts.add_an_account")}</div>
          <div className={GRP} data-testid="gmail-manual-add">
            <div className="px-1.5 py-1">
              <ConnectSetup
                c={c}
                cloud={cloud}
                onConnected={() => {
                  setShowManual(false);
                  onChanged();
                }}
                manualOnly
              />
            </div>
          </div>
        </>
      )}

      <FiltersGroup c={c} onChanged={onChanged} />

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        {t("gmail.filters_foot")}
      </div>
    </div>
  );
}

function AccountRow({ a, onChanged }: { a: GmailAccount; onChanged: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`gmail-account-${a.email}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate">{a.email}</span>
        {a.default && <span className={TAG_ACCENT}>{t("connector.default")}</span>}
        {a.needs_reauth && <span className={TAG_WARN}>{t("gmail.sign_in_again")}</span>}
      </span>
      {!a.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`gmail-make-default-${a.email}`}
          onClick={async () => {
            await setGmailDefaultAccount(a.email);
            onChanged();
          }}
        >
          {t("connector.make_default")}
        </button>
      )}
      <button
        className={XBTN}
        title={t("gmail.disconnect_mailbox_title")}
        data-testid={`gmail-disconnect-${a.email}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectGmailAccount(a.email);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}

function FiltersGroup({ c, onChanged }: Pick<DetailProps, "c" | "onChanged">) {
  const { t } = useTranslation();
  const filters = c.filters ?? { senders: [], labels: [] };
  return (
    <>
      <div className={GRP_H}>{t("gmail.never_show_agents")}</div>
      <div className={GRP} data-testid="gmail-filters">
        <ChipListRow
          label={t("gmail.senders")}
          testid="gmail-filter-senders"
          placeholder={t("gmail.senders_placeholder")}
          values={filters.senders}
          onSave={async (senders) => {
            await setGmailFilters({ senders });
            onChanged();
          }}
        />
        <ChipListRow
          label={t("gmail.labels")}
          testid="gmail-filter-labels"
          placeholder={t("gmail.labels_placeholder")}
          values={filters.labels}
          onSave={async (labels) => {
            await setGmailFilters({ labels });
            onChanged();
          }}
        />
      </div>
      <div className={FOOT}>
        {t("gmail.filters_foot_inner")}
      </div>
    </>
  );
}

function ChipListRow({
  label,
  testid,
  placeholder,
  values,
  onSave,
}: {
  label: string;
  testid: string;
  placeholder: string;
  values: string[];
  onSave: (next: string[]) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const add = async () => {
    const v = draft.trim();
    if (!v) return;
    setDraft("");
    await onSave([...values, v]);
  };
  return (
    <div className={ROW} data-testid={testid}>
      <span className={LABEL}>{label}</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-paper border border-line text-[13px]"
          >
            {v}
            <button
              className={XBTN}
              title={t("common.remove")}
              onClick={() => onSave(values.filter((x) => x !== v))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="flex-1 min-w-[140px] bg-transparent text-[13px] outline-none placeholder:text-faint"
          placeholder={placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          onBlur={() => draft.trim() && add()}
        />
      </span>
    </div>
  );
}
