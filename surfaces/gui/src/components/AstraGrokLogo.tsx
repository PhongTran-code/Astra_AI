export function AstraGrokLogo({
  size = 20,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <img
      src="/astra-mark.svg"
      width={size}
      height={size}
      alt="Astra"
      className={`inline-block select-none shrink-0 astra-mark-img ${className}`}
      style={{ width: size, height: size, objectFit: "contain" }}
    />
  );
}

export const AstraLogo = AstraGrokLogo;
