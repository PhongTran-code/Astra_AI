import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

const POSTER_WIDTH = 1080;
const POSTER_HEIGHT = 1620;

// The supplied poster is a fixed 1080×1620 composition. Scale its complete canvas into the
// login panel instead of cropping the source image, so every handoff layer stays visible.
export function ArcadePoster() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const fit = () => {
      const { width, height } = stage.getBoundingClientRect();
      if (!width || !height) return;
      setScale(Math.min(width / POSTER_WIDTH, height / POSTER_HEIGHT));
    };
    fit();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", fit);
      return () => window.removeEventListener("resize", fit);
    }
    const observer = new ResizeObserver(fit);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="arcade-poster-stage" ref={stageRef} aria-hidden="true">
      <div
        className="arcade-poster"
        data-testid="arcade-poster"
        style={{ "--arcade-poster-scale": scale } as CSSProperties}
      >
        <img className="arcade-poster-bg" src="/assets/arcade-login.png" alt="" />
        <div className="arcade-poster-decor arcade-poster-scanlines" />
        <div className="arcade-poster-decor arcade-poster-vignette" />
        <div className="arcade-poster-decor arcade-poster-frame" />

        <div className="arcade-poster-coin">
          <div>100%</div>
          <div>LOCAL</div>
          <div>+ PRIVATE</div>
        </div>
        <div className="arcade-poster-badge">
          <div>DEV+</div>
          <div>BRING<br />YOUR<br />REPO</div>
        </div>

        <div className="arcade-poster-composition">
          <header className="arcade-poster-top-strip">
            <span>◄ PLAYER&nbsp;1&nbsp;READY</span>
            <span>BUILD&nbsp;v0.4.1&nbsp;·&nbsp;OPEN&nbsp;BETA</span>
            <span>LOCAL&nbsp;MODE&nbsp;ON ►</span>
          </header>

          <div className="arcade-poster-title-block">
            <p className="arcade-poster-eyebrow">★ THE LOCAL-FIRST AI COWORKER ★</p>
            <h1 className="arcade-poster-title">ASTRA</h1>
            <p className="arcade-poster-insert">▶ PRESS START TO INSTALL ◀</p>
            <p className="arcade-poster-tagline">
              One laptop. Zero cloud calls. Infinite context.<br />
              <b>Local LLM · Agentic RAG · Code Review · Your Data Stays Home</b>
            </p>
            <div className="arcade-poster-season-chip">
              <span />
              OPEN BETA LIVE NOW · <em>OPEN&nbsp;SOURCE&nbsp;·&nbsp;FREE&nbsp;FOREVER</em>
            </div>
          </div>

          <section className="arcade-poster-bracket">
            <div className="arcade-poster-bracket-header">
              <span>◄ SHOWDOWN · ROUND 01</span>
              <span>ASTRA VS THE CLOUD</span>
              <span>WINNER: YOU ►</span>
            </div>
            <Match
              left={{ seed: "TEAM ASTRA · LOCAL", tag: "PRIVATE_BY_DEFAULT", lives: 3, power: "100%" }}
              right={{ seed: "TEAM CLOUD · REMOTE", tag: "SENDS_YOUR_CODE_AWAY", lives: 1, power: "0%" }}
            />
            <Match
              left={{ seed: "TEAM ASTRA · OFFLINE", tag: "WORKS_ON_A_PLANE", lives: 3, power: "∞" }}
              right={{ seed: "TEAM CLOUD · ONLINE", tag: "NEEDS_5G_TO_THINK", lives: 1, power: "4G?" }}
            />
            <Match
              left={{ seed: "TEAM ASTRA · FREE", tag: "MIT_LICENSED_HERO", lives: 3, power: "$0" }}
              right={{ seed: "TEAM CLOUD · PAID", tag: "METERED_TOKEN_TAX", lives: 1, power: "$$$" }}
            />
          </section>

          <div className="arcade-poster-spacer" />
          <footer className="arcade-poster-footer">
            <div>
              <div>◄ RELEASE</div>
              <strong>OUT&nbsp;NOW<br />v0.4.1</strong>
              <small>Open beta · Weekly patches · 2026</small>
            </div>
            <div>
              <div>RUNS ON</div>
              <strong>MAC&nbsp;·&nbsp;LINUX<br />&nbsp;WINDOWS&nbsp;</strong>
              <small>8GB RAM · Apple Silicon / x64</small>
            </div>
            <div>
              <div>INSTALL ►</div>
              <strong>brew&nbsp;install<br />astra</strong>
              <small>or clone · star · fork on GitHub</small>
            </div>
            <div className="arcade-poster-credits">
              <span>© 2026 Phong Tran · HCMUS</span>
              <span>GITHUB.COM/PHONGTRAN-CODE/ASTRA_AI</span>
              <span>#LOCALFIRST</span>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}

type Player = { seed: string; tag: string; lives: number; power: string };

function Match({ left, right }: { left: Player; right: Player }) {
  return (
    <>
      <PlayerCard side="left" player={left} />
      <div className="arcade-poster-vs">VS</div>
      <PlayerCard side="right" player={right} />
    </>
  );
}

function PlayerCard({ side, player }: { side: "left" | "right"; player: Player }) {
  return (
    <div className={`arcade-poster-player-wrap arcade-poster-player-${side}`}>
      <div>{player.seed}</div>
      <div>
        <span>{player.tag}</span>
        <span>{Array.from({ length: player.lives }, (_, index) => <i key={index} />)}</span>
        <strong>{player.power}</strong>
      </div>
    </div>
  );
}
