// A small on/off switch (mock's `.tgl` / `.knob`) as an accessible button[role=switch]. Driven by
// props so it's testable (query by role "switch", assert aria-checked, fireEvent.click to flip).
// Reused by the persona detail page (default-connection + enable toggles) and the Sources drawer.

export function Toggle({
  checked,
  onChange,
  disabled,
  title,
  ariaLabel,
  tone = "accent",
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
  tone?: "accent" | "success";
}) {
  return (
    <button
      type="button"
      className={"tgl" + (checked ? " on" : "") + (tone === "success" ? " tgl-success" : "")}
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      title={title}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
    >
      <span className="knob" />
    </button>
  );
}
