const AVATAR_COUNT = 30;

type Accessory = "orb" | "heart" | "bolt" | "star" | "flower" | "leaf" | "crown" | "spark";
type Expression = "bright" | "happy" | "wink" | "curious" | "heart";

type AvatarPalette = {
  body: string;
  shade: string;
  outline: string;
  highlight: string;
  accessory: string;
  cheek: string;
};

const PALETTES: AvatarPalette[] = [
  { body: "#19d8ef", shade: "#078cc9", outline: "#18206d", highlight: "#b9fbff", accessory: "#27a6ff", cheek: "#f19ed8" },
  { body: "#ff3d9a", shade: "#c51c78", outline: "#5b145d", highlight: "#ffd2f1", accessory: "#ff7bc4", cheek: "#ffb3d9" },
  { body: "#a4ee24", shade: "#4dbd38", outline: "#174a4e", highlight: "#eaffad", accessory: "#ffe04b", cheek: "#ff9fba" },
  { body: "#8c4dff", shade: "#4c2fbd", outline: "#241b76", highlight: "#dfc4ff", accessory: "#29e4ff", cheek: "#ff91d4" },
  { body: "#ff862d", shade: "#d9481e", outline: "#6b1940", highlight: "#ffd29e", accessory: "#6fe6ff", cheek: "#ffb28f" },
  { body: "#ffd82e", shade: "#f29a18", outline: "#734126", highlight: "#fff1a3", accessory: "#ff7c29", cheek: "#ff9f9a" },
  { body: "#3c9dff", shade: "#2861c5", outline: "#152b73", highlight: "#b8e7ff", accessory: "#ff6bd6", cheek: "#ffb4dc" },
  { body: "#ff6681", shade: "#cb315e", outline: "#5e214d", highlight: "#ffd0d6", accessory: "#ffe052", cheek: "#ffb6a4" },
  { body: "#42e0b6", shade: "#169c9a", outline: "#164866", highlight: "#c5fff0", accessory: "#ff78c8", cheek: "#ffafd7" },
  { body: "#c790ff", shade: "#8051d4", outline: "#3b256e", highlight: "#f1dcff", accessory: "#7df5ff", cheek: "#ffb2dc" },
];

const VARIANTS: Array<{ palette: number; accessory: Accessory; expression: Expression }> = [
  { palette: 0, accessory: "orb", expression: "bright" },
  { palette: 1, accessory: "heart", expression: "happy" },
  { palette: 2, accessory: "bolt", expression: "curious" },
  { palette: 3, accessory: "star", expression: "wink" },
  { palette: 4, accessory: "flower", expression: "bright" },
  { palette: 5, accessory: "spark", expression: "happy" },
  { palette: 6, accessory: "leaf", expression: "curious" },
  { palette: 7, accessory: "crown", expression: "wink" },
  { palette: 8, accessory: "heart", expression: "heart" },
  { palette: 9, accessory: "orb", expression: "happy" },
  { palette: 0, accessory: "star", expression: "curious" },
  { palette: 1, accessory: "bolt", expression: "wink" },
  { palette: 2, accessory: "flower", expression: "heart" },
  { palette: 3, accessory: "leaf", expression: "bright" },
  { palette: 4, accessory: "crown", expression: "happy" },
  { palette: 5, accessory: "heart", expression: "curious" },
  { palette: 6, accessory: "spark", expression: "wink" },
  { palette: 7, accessory: "orb", expression: "heart" },
  { palette: 8, accessory: "bolt", expression: "bright" },
  { palette: 9, accessory: "flower", expression: "happy" },
  { palette: 0, accessory: "leaf", expression: "wink" },
  { palette: 1, accessory: "crown", expression: "curious" },
  { palette: 2, accessory: "heart", expression: "bright" },
  { palette: 3, accessory: "orb", expression: "heart" },
  { palette: 4, accessory: "star", expression: "happy" },
  { palette: 5, accessory: "leaf", expression: "curious" },
  { palette: 6, accessory: "flower", expression: "bright" },
  { palette: 7, accessory: "spark", expression: "heart" },
  { palette: 8, accessory: "crown", expression: "happy" },
  { palette: 9, accessory: "bolt", expression: "wink" },
];

type ReferenceEye = "classic" | "wide" | "happy" | "wink" | "sleepy" | "heart" | "star";
type ReferenceAntenna = "orb" | "heart" | "bolt" | "spark" | "leaf" | "crown" | "ring" | "none";
type ReferenceDetail = "screen" | "blush" | "badge" | "dots" | "stripe" | "none";

type ReferenceGhostPalette = {
  body: string;
  shade: string;
  outline: string;
  highlight: string;
  accent: string;
};

type ReferenceGhostVariant = {
  palette: number;
  body: number;
  eyes: ReferenceEye;
  antenna: ReferenceAntenna;
  detail: ReferenceDetail;
};

const REFERENCE_PALETTES: ReferenceGhostPalette[] = [
  { body: "#25E4F4", shade: "#087CB5", outline: "#071344", highlight: "#A7FBFF", accent: "#2D9CFF" },
  { body: "#A8F13A", shade: "#3E9D45", outline: "#153E45", highlight: "#E8FFAE", accent: "#FFE34F" },
  { body: "#FF4EAB", shade: "#B71971", outline: "#4B155D", highlight: "#FFD2F1", accent: "#FF8ACB" },
  { body: "#9C61FF", shade: "#4C2EB2", outline: "#211852", highlight: "#E2C7FF", accent: "#37E8FF" },
  { body: "#FFC928", shade: "#DC8517", outline: "#5C3421", highlight: "#FFF2A3", accent: "#FF6E35" },
  { body: "#FF793D", shade: "#C94425", outline: "#571A36", highlight: "#FFD3A8", accent: "#70E8FF" },
  { body: "#45A9FF", shade: "#2861B5", outline: "#152864", highlight: "#BDEBFF", accent: "#FF73D2" },
  { body: "#36D5AC", shade: "#167D85", outline: "#123E58", highlight: "#C2FFF0", accent: "#FF76C9" },
  { body: "#FF7387", shade: "#C9375B", outline: "#552047", highlight: "#FFD2D9", accent: "#FFE45B" },
  { body: "#C7A0FF", shade: "#7448C3", outline: "#34205F", highlight: "#F0DFFF", accent: "#77F5FF" },
  { body: "#7EDBFF", shade: "#3589C4", outline: "#173866", highlight: "#E2FCFF", accent: "#FF70AF" },
  { body: "#D8F04B", shade: "#6CA52A", outline: "#294122", highlight: "#F6FFB6", accent: "#FF9E3D" },
];

const REFERENCE_BODIES = [
  {
    outer: "M19 14h5v-4h16v4h5v4h5v6h4v29h-6v5h-7v-4h-6v7h-7v-7h-7v4h-7v-5h-6V23h4v-6h5z",
    inner: "M21 17h5v-4h12v4h5v4h4v6h3v23h-5v5h-6v-7h-5v8h-5v-8h-6v7h-6v-5h-4V25h4v-5h4z",
    shade: "M17 36h4v14h6v5h-6v-4h-4zM43 50h7v-5h3v5h-5v5h-5z",
  },
  {
    outer: "M14 20h5v-6h26v6h5v5h5v26h-6v5h-7v-4h-7v7h-7v-7h-7v4h-7v-5h-6V25h6v-5z",
    inner: "M17 22h5v-5h22v5h5v5h4v22h-5v5h-7v-6h-7v8h-5v-8h-8v6h-6v-5h-4V27h6z",
    shade: "M17 37h5v12h6v5h-6v-4h-5zM43 49h7v-5h3v5h-4v5h-6z",
  },
  {
    outer: "M21 14h4v-6h14v6h5v5h5v7h4v29h-6v5h-7v-5h-5v7h-6v-7h-7v5h-6v-5h-5V26h4v-7h5z",
    inner: "M23 17h5v-5h8v5h5v5h4v7h4v23h-5v5h-7v-6h-5v8h-4v-8h-7v6h-6v-5h-4V28h4v-7h4z",
    shade: "M18 38h5v12h6v5h-6v-4h-5zM42 50h7v-5h3v5h-4v5h-6z",
  },
  {
    outer: "M18 16h5v-5h18v5h5v5h5v7h4v25h-6v5h-8v-5h-5v7h-7v-7h-7v5h-7v-5h-5V28h4v-7h4z",
    inner: "M21 19h5v-5h14v5h5v5h4v7h3v19h-5v5h-7v-6h-5v8h-5v-8h-7v6h-6v-5h-4V30h4v-6h4z",
    shade: "M20 39h5v11h6v5h-6v-4h-5zM41 50h8v-5h3v5h-5v5h-6z",
  },
];

const REFERENCE_VARIANTS: ReferenceGhostVariant[] = [
  { palette: 0, body: 0, eyes: "classic", antenna: "orb", detail: "screen" },
  { palette: 1, body: 1, eyes: "wide", antenna: "leaf", detail: "blush" },
  { palette: 2, body: 3, eyes: "happy", antenna: "heart", detail: "stripe" },
  { palette: 3, body: 2, eyes: "wink", antenna: "spark", detail: "badge" },
  { palette: 4, body: 0, eyes: "sleepy", antenna: "crown", detail: "dots" },
  { palette: 5, body: 1, eyes: "star", antenna: "bolt", detail: "screen" },
  { palette: 6, body: 2, eyes: "heart", antenna: "ring", detail: "blush" },
  { palette: 7, body: 3, eyes: "classic", antenna: "none", detail: "badge" },
  { palette: 8, body: 1, eyes: "wide", antenna: "spark", detail: "stripe" },
  { palette: 9, body: 0, eyes: "wink", antenna: "orb", detail: "dots" },
  { palette: 10, body: 2, eyes: "happy", antenna: "leaf", detail: "screen" },
  { palette: 11, body: 3, eyes: "sleepy", antenna: "heart", detail: "none" },
  { palette: 0, body: 1, eyes: "star", antenna: "bolt", detail: "blush" },
  { palette: 1, body: 2, eyes: "heart", antenna: "crown", detail: "stripe" },
  { palette: 2, body: 0, eyes: "classic", antenna: "ring", detail: "badge" },
  { palette: 3, body: 3, eyes: "wide", antenna: "none", detail: "dots" },
  { palette: 4, body: 2, eyes: "wink", antenna: "spark", detail: "screen" },
  { palette: 5, body: 0, eyes: "happy", antenna: "leaf", detail: "blush" },
  { palette: 6, body: 1, eyes: "sleepy", antenna: "heart", detail: "stripe" },
  { palette: 7, body: 2, eyes: "star", antenna: "orb", detail: "badge" },
  { palette: 8, body: 3, eyes: "heart", antenna: "bolt", detail: "none" },
  { palette: 9, body: 1, eyes: "classic", antenna: "crown", detail: "screen" },
  { palette: 10, body: 0, eyes: "wide", antenna: "ring", detail: "dots" },
  { palette: 11, body: 2, eyes: "wink", antenna: "none", detail: "blush" },
  { palette: 0, body: 3, eyes: "happy", antenna: "spark", detail: "stripe" },
  { palette: 1, body: 0, eyes: "sleepy", antenna: "leaf", detail: "badge" },
  { palette: 2, body: 1, eyes: "star", antenna: "heart", detail: "screen" },
  { palette: 3, body: 2, eyes: "heart", antenna: "orb", detail: "dots" },
  { palette: 4, body: 3, eyes: "classic", antenna: "bolt", detail: "blush" },
  { palette: 5, body: 1, eyes: "wide", antenna: "crown", detail: "none" },
];

function stableIndex(value: string, max: number): number {
  let hash = 0;
  for (let index = 0; index < value.length; index++) {
    hash = (hash << 5) - hash + value.charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash) % max;
}

function AccessoryMark({ type, color, highlight }: { type: Accessory; color: string; highlight: string }) {
  if (type === "heart") {
    return <path d="M43 12h5V9h6v3h5v8h-4v4h-8v-4h-4z" fill={color} />;
  }
  if (type === "bolt") {
    return <path d="M50 7h8l-4 8h6L48 26l3-8h-6z" fill={color} />;
  }
  if (type === "star") {
    return <path d="M50 7l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z" fill={color} />;
  }
  if (type === "flower") {
    return <g fill={color}><path d="M47 8h6v6h-6zM40 15h6v6h-6zM54 15h6v6h-6zM47 22h6v6h-6z" /><path d="M47 15h6v6h-6z" fill={highlight} /></g>;
  }
  if (type === "leaf") {
    return <path d="M47 8h11v6h-3v6h-5v5h-6v-8h3z" fill={color} />;
  }
  if (type === "crown") {
    return <path d="M39 11h5l3 5 3-8 3 8 4-5 4 3-3 11H42z" fill={color} />;
  }
  if (type === "spark") {
    return <path d="M47 7h6v7h7v6h-7v7h-6v-7h-7v-6h7z" fill={color} />;
  }
  return <g><path d="M44 11h12v12H44z" fill={color} /><path d="M47 7h6v4h-6z" fill={color} /><path d="M47 13h4v4h-4z" fill={highlight} /></g>;
}

function Eyes({ expression, outline, highlight }: { expression: Expression; outline: string; highlight: string }) {
  if (expression === "happy") {
    return <g fill={outline}><path d="M30 48h4v-3h6v3h4v3h-4v3h-6v-3h-4z" /><path d="M54 48h4v-3h6v3h4v3h-4v3h-6v-3h-4z" /></g>;
  }
  if (expression === "wink") {
    return <g><path d="M29 42h17v18H29zM54 42h17v18H54z" fill="#fff" /><path d="M35 48h7v8h-7zM58 50h9v3h-9z" fill={outline} /><path d="M37 49h3v3h-3z" fill={highlight} /></g>;
  }
  if (expression === "heart") {
    return <g><path d="M29 42h17v18H29zM54 42h17v18H54z" fill="#fff" /><path d="M35 48h3v-2h3v2h3v4h-3v3h-3v-3h-3zM59 48h3v-2h3v2h3v4h-3v3h-3v-3h-3z" fill={outline} /></g>;
  }
  const offset = expression === "curious" ? 2 : 0;
  return <g><path d="M29 42h17v18H29zM54 42h17v18H54z" fill="#fff" /><path d={`M${35 + offset} 47h8v10h-8zM${59 + offset} 47h8v10h-8z`} fill={outline} /><path d={`M${37 + offset} 48h3v3h-3zM${61 + offset} 48h3v3h-3z`} fill={highlight} /></g>;
}

function ReferenceAntenna({ type, color, highlight }: { type: ReferenceAntenna; color: string; highlight: string }) {
  if (type === "none") return null;
  if (type === "heart") {
    return <g><path d="M31 13h2V7h2v6h2v2h-6z" fill="#071344" /><path d="M28 3h3V1h4v2h3v5h-3v3h-4V8h-3z" fill={color} /><path d="M31 4h3v3h-3z" fill={highlight} /></g>;
  }
  if (type === "bolt") {
    return <g><path d="M31 14h3V8h-3z" fill="#071344" /><path d="M32 1h7l-4 6h4l-8 9 2-7h-4z" fill={color} /><path d="M34 3h2l-2 3h-2z" fill={highlight} /></g>;
  }
  if (type === "spark") {
    return <g><path d="M31 14h3V8h-3z" fill="#071344" /><path d="M31 1h3v4h4v3h-4v4h-3V8h-4V5h4z" fill={color} /><path d="M31 3h2v2h-2z" fill={highlight} /></g>;
  }
  if (type === "leaf") {
    return <g><path d="M31 14h3V8h-3z" fill="#071344" /><path d="M32 1h8v4h-3v4h-5V5h3V3h-3z" fill={color} /><path d="M34 3h3v2h-3z" fill={highlight} /></g>;
  }
  if (type === "crown") {
    return <g><path d="M31 14h3V8h-3z" fill="#071344" /><path d="M27 2h3l2 4 2-6 2 6 3-4 2 2-2 7H29z" fill={color} /><path d="M31 5h2v2h-2z" fill={highlight} /></g>;
  }
  if (type === "ring") {
    return <g><path d="M31 14h3V9h-3z" fill="#071344" /><path d="M28 1h9v3h3v6h-3v3h-9v-3h-3V4h3z" fill={color} /><path d="M30 4h5v5h-5z" fill={highlight} /></g>;
  }
  return <g><path d="M31 14h3V8h-3z" fill="#071344" /><circle cx="32.5" cy="5" r="4" fill={color} /><path d="M31 3h3v3h-3z" fill={highlight} /></g>;
}

function ReferenceEyes({ type, outline, highlight, accent }: { type: ReferenceEye; outline: string; highlight: string; accent: string }) {
  if (type === "happy") {
    return <g fill={outline}><path d="M20 32h3v-3h5v3h3v3h-3v3h-5v-3h-3z" /><path d="M36 32h3v-3h5v3h3v3h-3v3h-5v-3h-3z" /></g>;
  }
  if (type === "wink") {
    return <g><path d="M20 28h11v12H20zM36 28h11v12H36z" fill={outline} /><path d="M23 31h5v6h-5z" fill={highlight} /><path d="M38 33h8v3h-8z" fill={highlight} /></g>;
  }
  if (type === "sleepy") {
    return <g><path d="M20 30h11v7H20zM36 30h11v7H36z" fill={outline} /><path d="M23 31h3v2h-3zM39 31h3v2h-3z" fill={highlight} /></g>;
  }
  if (type === "heart") {
    return <g fill={outline}><path d="M20 29h3v-2h3v2h3v4h-3v3h-3v-3h-3z" /><path d="M36 29h3v-2h3v2h3v4h-3v3h-3v-3h-3z" /></g>;
  }
  if (type === "star") {
    return <g><path d="M20 28h11v12H20zM36 28h11v12H36z" fill={outline} /><path d="M25 29l1 3h3l-2 2 1 3-3-2-3 2 1-3-2-2h3zM41 29l1 3h3l-2 2 1 3-3-2-3 2 1-3-2-2h3z" fill={accent} /></g>;
  }
  if (type === "wide") {
    return <g><path d="M19 27h13v14H19zM35 27h13v14H35z" fill="#F8FEFF" /><path d="M22 30h7v8h-7zM38 30h7v8h-7z" fill={outline} /><path d="M24 31h3v3h-3zM40 31h3v3h-3z" fill={highlight} /></g>;
  }
  return <g><path d="M20 28h11v12H20zM36 28h11v12H36z" fill={outline} /><path d="M23 30h5v7h-5zM39 30h5v7h-5z" fill={highlight} /><path d="M24 31h2v2h-2zM40 31h2v2h-2z" fill="#FFFFFF" /></g>;
}

function ReferenceDetailMark({ type, palette }: { type: ReferenceDetail; palette: ReferenceGhostPalette }) {
  if (type === "blush") {
    return <g fill={palette.accent}><path d="M16 43h4v2h-4zM20 45h4v2h-4zM48 45h4v2h-4zM52 43h4v2h-4z" /></g>;
  }
  if (type === "badge") {
    return <g><path d="M29 43h7v7h-7z" fill={palette.outline} /><path d="M31 45h3v3h-3z" fill={palette.accent} /></g>;
  }
  if (type === "dots") {
    return <g fill={palette.accent}><path d="M25 44h3v3h-3zM37 44h3v3h-3z" /></g>;
  }
  if (type === "stripe") {
    return <path d="M17 47h6v2h-6zM43 47h6v2h-6z" fill={palette.highlight} opacity=".85" />;
  }
  if (type === "screen") {
    return <g><path d="M27 44h18v7H27z" fill={palette.outline} /><path d="M30 46h12v3H30z" fill={palette.accent} /><path d="M32 46h3v2h-3z" fill={palette.highlight} /></g>;
  }
  return null;
}

function ReferenceGhostAvatar({
  className,
  online,
  title,
  variantIndex,
}: {
  className: string;
  online: boolean;
  title: string;
  variantIndex: number;
}) {
  const selected = REFERENCE_VARIANTS[variantIndex % REFERENCE_VARIANTS.length];
  const palette = REFERENCE_PALETTES[selected.palette];
  const body = REFERENCE_BODIES[selected.body];
  const glowId = `astra-reference-glow-${variantIndex}`;
  const bodyGradientId = `astra-reference-body-${variantIndex}`;

  return (
    <span
      className={`relative block shrink-0 overflow-visible ${className}`}
      title={title}
      data-testid="astra-bot-avatar"
      data-avatar-variant={variantIndex}
      aria-label={title}
      role="img"
    >
      <svg
        className="block h-full w-full"
        viewBox="0 0 64 64"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        shapeRendering="crispEdges"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <filter id={glowId} x="-45%" y="-45%" width="190%" height="190%">
            <feGaussianBlur stdDeviation="2.2" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <linearGradient id={bodyGradientId} x1="18" y1="12" x2="45" y2="55" gradientUnits="userSpaceOnUse">
            <stop stopColor={palette.body} />
            <stop offset="1" stopColor={palette.shade} />
          </linearGradient>
        </defs>
        <ReferenceAntenna type={selected.antenna} color={palette.accent} highlight={palette.highlight} />
        <path d={body.outer} fill={palette.body} opacity=".35" filter={`url(#${glowId})`} />
        <path d={body.outer} fill={palette.outline} />
        <path d={body.inner} fill={`url(#${bodyGradientId})`} />
        <path d={body.shade} fill={palette.shade} />
        <path d="M21 22h30v2H21zM17 43h36v2H17zM21 49h29v2H21z" fill={palette.highlight} opacity=".15" />
        <path d="M23 18h7v-3h7v3h-7v4h-7z" fill={palette.highlight} />
        <path d="M19 24h4v7h-4z" fill={palette.highlight} opacity=".8" />
        <ReferenceEyes type={selected.eyes} outline={palette.outline} highlight={palette.highlight} accent={palette.accent} />
        <ReferenceDetailMark type={selected.detail} palette={palette} />
        <path d="M25 53h5v3h-5zM38 53h5v3h-5z" fill={palette.highlight} opacity=".7" />
      </svg>
      {online && <span className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-panel bg-ok" aria-hidden="true" />}
    </span>
  );
}

/** Static code-native SVG avatar. The same stable identity mapping keeps each agent's face consistent. */
export function AstraBotAvatar({
  identity = "",
  role = "",
  className = "h-10 w-10",
  online = false,
  title = "Astra agent",
  design = "catalog",
}: {
  identity?: string;
  role?: string;
  className?: string;
  online?: boolean;
  title?: string;
  design?: "catalog" | "referenceGhost";
}) {
  const identityKey = identity || role || "astra";

  if (design === "referenceGhost") {
    return (
      <ReferenceGhostAvatar
        className={className}
        online={online}
        title={title}
        variantIndex={stableIndex(identityKey, REFERENCE_VARIANTS.length)}
      />
    );
  }

  const variant = stableIndex(identityKey, AVATAR_COUNT);
  const selected = VARIANTS[variant];
  const palette = PALETTES[selected.palette];

  return (
    <span
      className={`relative block shrink-0 overflow-visible ${className}`}
      title={title}
      data-testid="astra-bot-avatar"
      aria-label={title}
      role="img"
    >
      <svg
        className="block h-full w-full"
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        shapeRendering="crispEdges"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M45 29h10v8H45z" fill={palette.outline} />
        <path d="M47 26h6v8h-6z" fill={palette.shade} />
        <AccessoryMark type={selected.accessory} color={palette.accessory} highlight={palette.highlight} />
        <path d="M19 43h8v23h-8zM73 43h8v23h-8z" fill={palette.outline} />
        <path d="M17 48h8v13h-8zM75 48h8v13h-8z" fill={palette.shade} />
        <path d="M24 35h4v-7h8v-5h28v5h8v7h4v39h-7v7H61v-7h-8v8h-6v-8h-8v7H27v-7h-7V35z" fill={palette.outline} />
        <path d="M28 37h4v-6h7v-4h22v4h7v6h4v33h-7v6h-7v-7H46v7h-7v-7h-9v7h-6V37z" fill={palette.body} />
        <path d="M25 61h7v9h7v6h-6v-5h-8zM68 61h7v9h-7v6h-7v-6h7z" fill={palette.shade} />
        <path d="M31 35h5v-4h8v-3h6v3h-6v4h-8v5h-5z" fill={palette.highlight} />
        <Eyes expression={selected.expression} outline={palette.outline} highlight={palette.highlight} />
        <path d="M27 63h5v3h-5zM68 63h5v3h-5z" fill={palette.cheek} />
        {selected.expression === "happy" || selected.expression === "heart" ? (
          <path d="M42 66h4v3h8v-3h4v5h-4v3h-8v-3h-4z" fill={palette.outline} />
        ) : (
          <path d="M44 67h12v3H44z" fill={palette.outline} />
        )}
        <path d="M38 74h24v9H38z" fill={palette.outline} />
        <path d="M42 77h16v4H42z" fill={palette.accessory} />
        <path d="M45 77h4v2h-4z" fill={palette.highlight} />
      </svg>
      {online && <span className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-panel bg-ok" aria-hidden="true" />}
    </span>
  );
}
