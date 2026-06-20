import { useState, type ReactNode } from "react";
import SportsDashBall from "../logo/SportsDashBall";
import { Wordmark, HorizontalLockup, StackedLockup } from "../logo/Wordmark";
import SportsDashSpinner from "./SportsDashSpinner";
import SportsDashSplash from "./SportsDashSplash";
import SetupLoader from "./SetupLoader";
import MiniSpinner from "./MiniSpinner";

/**
 * Living demo of the SportsDash logo + loader system — the React equivalent of
 * the `SportsDash Loaders.dc.html` prototype. Reachable in dev/preview at
 * `/#sd-loaders` (App swaps it in when the hash matches). Not part of the app's
 * routed surface.
 */

const MONO: React.CSSProperties = {
  fontFamily: "var(--sd-font-mono)",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.14em",
  color: "#8A877C",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={MONO}>{title}</h2>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 32,
          padding: 28,
          borderRadius: 16,
          border: "1px solid #24262C",
          background: "#15171C",
        }}
      >
        {children}
      </div>
    </section>
  );
}

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
      }}
    >
      {children}
      <span style={{ ...MONO, fontSize: 10 }}>{label}</span>
    </div>
  );
}

export default function LoaderGallery() {
  const [splashOpen, setSplashOpen] = useState(false);

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#101216",
        color: "#F4F1EC",
        padding: "48px 32px",
        fontFamily: "var(--sd-font-display)",
      }}
    >
      <div
        style={{
          maxWidth: 1080,
          margin: "0 auto",
          display: "flex",
          flexDirection: "column",
          gap: 40,
        }}
      >
        <header style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <HorizontalLockup size={52} sportsColor="#F4F1EC" />
          <span style={MONO}>Loader &amp; logo system · /#sd-loaders</span>
        </header>

        <Section title="Ball mark — detailed">
          <Tile label="120">
            <SportsDashBall size={120} />
          </Tile>
          <Tile label="64">
            <SportsDashBall size={64} />
          </Tile>
          <Tile label="40">
            <SportsDashBall size={40} />
          </Tile>
          <Tile label="28">
            <SportsDashBall size={28} />
          </Tile>
          <Tile label="96 · on ink">
            <div style={{ background: "#15171C", padding: 8, borderRadius: 10 }}>
              <SportsDashBall size={96} onDark />
            </div>
          </Tile>
        </Section>

        <Section title="Ball mark — flat (≤28px)">
          <Tile label="28">
            <SportsDashBall size={28} variant="flat" />
          </Tile>
          <Tile label="20">
            <SportsDashBall size={20} variant="flat" />
          </Tile>
          <Tile label="18">
            <SportsDashBall size={18} variant="flat" />
          </Tile>
          <Tile label="16">
            <SportsDashBall size={16} variant="flat" />
          </Tile>
        </Section>

        <Section title="Wordmark & lockups">
          <Wordmark size={34} sportsColor="#F4F1EC" />
          <HorizontalLockup size={44} sportsColor="#F4F1EC" />
          <StackedLockup size={84} sportsColor="#F4F1EC" />
          <div style={{ background: "#FFFFFF", padding: "18px 22px", borderRadius: 12 }}>
            <HorizontalLockup size={40} />
          </div>
        </Section>

        <Section title="Prompt 1 — everyday spinner">
          <SportsDashSpinner size={96} label="Loading" />
          <SportsDashSpinner size={56} />
        </Section>

        <Section title="Prompt 4 — mini spinner (flat)">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 13,
              color: "#9B988E",
              fontFamily: "var(--sd-font-mono)",
            }}
          >
            <MiniSpinner size={18} />
            <span>syncing scores…</span>
          </div>
          <MiniSpinner size={16} />
          <MiniSpinner size={20} />
        </Section>

        <Section title="Prompt 3 — setup loader">
          <SetupLoader />
        </Section>
        <Section title="Prompt 3 — setup loader (controlled)">
          <SetupLoader progress={0.5} status="Importing your followed teams…" />
        </Section>

        <Section title="Prompt 2 — cold-start splash">
          <button
            type="button"
            onClick={() => setSplashOpen(true)}
            style={{
              fontFamily: "var(--sd-font-mono)",
              fontSize: 12,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              color: "#15171C",
              background: "#E8643C",
              border: "none",
              borderRadius: 8,
              padding: "10px 16px",
              cursor: "pointer",
            }}
          >
            Launch full-screen splash
          </button>
        </Section>
      </div>

      {splashOpen ? (
        <SportsDashSplash>
          <button
            type="button"
            onClick={() => setSplashOpen(false)}
            style={{
              marginTop: 14,
              fontFamily: "var(--sd-font-mono)",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.14em",
              color: "#7D7A70",
              background: "transparent",
              border: "1px solid #2B2D33",
              borderRadius: 8,
              padding: "8px 14px",
              cursor: "pointer",
            }}
          >
            Close
          </button>
        </SportsDashSplash>
      ) : null}
    </div>
  );
}
