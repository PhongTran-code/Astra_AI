import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { announceCloudChanged, cloudLogin, getCloudStatus, waitForCloudSignIn } from "../api";
import { ArcadePoster } from "./ArcadePoster";
import { Icon } from "./Icon";

type SignInState = "idle" | "opening" | "waiting" | "success" | "error";

interface Props {
  open: boolean;
  onClose: () => void;
}

// This is a focused product surface around the existing Cloud OAuth flow. The sidecar still
// owns the browser redirect and SecretStore; this component only explains progress in the app.
export function CloudLoginScreen({ open, onClose }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<SignInState>("idle");
  const [account, setAccount] = useState("");
  const cancelRef = useRef<(() => void) | null>(null);
  const successTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearPending = () => {
    cancelRef.current?.();
    cancelRef.current = null;
    if (successTimerRef.current) clearTimeout(successTimerRef.current);
    successTimerRef.current = null;
  };

  useEffect(() => {
    if (!open) {
      clearPending();
      setState("idle");
      setAccount("");
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    getCloudStatus()
      .then((status) => {
        if (!status.signed_in) return;
        setAccount(status.account);
        setState("success");
        successTimerRef.current = setTimeout(onClose, 1100);
      })
      .catch(() => {});

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      clearPending();
    };
  }, [open, onClose]);

  const beginSignIn = async () => {
    if (state === "opening" || state === "waiting") return;
    clearPending();
    setState("opening");
    try {
      const result = await cloudLogin();
      if (!result.ok) throw new Error("Cloud sign-in could not start");
      setState("waiting");
      cancelRef.current = waitForCloudSignIn((status) => {
        cancelRef.current = null;
        if (!status?.signed_in) {
          setState("error");
          return;
        }
        setAccount(status.account);
        setState("success");
        announceCloudChanged();
        successTimerRef.current = setTimeout(onClose, 1100);
      });
    } catch {
      setState("error");
    }
  };

  if (!open) return null;

  const waiting = state === "opening" || state === "waiting";
  const isSuccess = state === "success";

  return (
    <div className="cloud-login-overlay" role="presentation">
      <section className="cloud-login-art" aria-label={t("login.art_label")}><ArcadePoster /></section>

      <section className="cloud-login-panel">
        <div className="cloud-login-card" role="dialog" aria-modal="true" aria-labelledby="cloud-login-title">

          <div className="cloud-login-brand">
            <Icon name="logo" size={27} />
            <span>Astra</span>
          </div>

          {isSuccess ? (
            <div className="cloud-login-success" aria-live="polite">
              <span className="cloud-login-success-mark"><Icon name="check" size={18} /></span>
              <h1 id="cloud-login-title">{t("login.success_title")}</h1>
              <p>{t("login.success_body", { account })}</p>
              <button
                type="button"
                className="cloud-login-primary"
                onClick={onClose}
                autoFocus
              >
                <span>{t("common.continue", "Continue")}</span>
              </button>
            </div>
          ) : (
            <>
              <div className="cloud-login-copy">
                <p className="cloud-login-eyebrow">{t("login.eyebrow")}</p>
                <h1 id="cloud-login-title">{t("login.title")}</h1>
                <p>{t("login.body")}</p>
              </div>

              <button
                type="button"
                className="cloud-login-primary"
                data-testid="cloud-login-primary"
                onClick={beginSignIn}
                disabled={waiting}
                autoFocus
              >
                {waiting && <span className="cloud-login-spinner" aria-hidden="true" />}
                <span>{waiting ? t("login.waiting") : t("login.continue")}</span>
              </button>
              <p className="cloud-login-status" aria-live="polite">
                {state === "waiting" || state === "opening"
                  ? t("login.browser_hint")
                  : state === "error"
                    ? t("login.error")
                    : t("login.security_hint")}
              </p>
            </>
          )}

          <div className="cloud-login-divider" />
          <button type="button" className="cloud-login-skip" onClick={onClose}>
            {t("login.skip")}
          </button>
        </div>
      </section>
    </div>
  );
}
