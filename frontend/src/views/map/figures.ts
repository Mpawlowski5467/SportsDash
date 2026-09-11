/** Original vector artwork: scales cleanly on retina displays, no font glyphs. */
export const PLANE_SVG = `<svg viewBox="0 0 48 56" width="30" height="35" aria-hidden="true" focusable="false">
  <g fill="none" stroke="#e2e8f0" stroke-linecap="round">
    <path d="M20 45v7M28 45v7" opacity=".25" stroke-width="1.5"/>
    <path d="M20 46v3M28 46v3" opacity=".65"/>
  </g>
  <g stroke="#475569" stroke-width=".8" stroke-linejoin="round">
    <path fill="#cbd5e1" d="m21 19-18 14v4l19-6h4l19 6v-4L27 19z"/>
    <path fill="#f1f5f9" stroke="none" d="m21 20-17 13 17-5zm6 0 17 13-17-5z"/>
    <path fill="currentColor" stroke="none" d="m3 33 2-1v4l-2 1zm42 0-2-1v4l2 1z"/>
    <rect x="12" y="26" width="3.5" height="8" rx="1.7" fill="#e2e8f0"/>
    <rect x="32.5" y="26" width="3.5" height="8" rx="1.7" fill="#e2e8f0"/>
    <path d="M12.5 27.5h2.5m18 0h2.5" stroke="#334155" stroke-width="1.5"/>
    <path fill="#cbd5e1" d="m22 38-9 7v2l10-2h2l10 2v-2l-9-7z"/>
    <path fill="#f8fafc" d="M24 3c-2.3 0-3.3 4.4-3.3 7.8v24.8c0 5.2 1.6 10.4 3.3 10.4s3.3-5.2 3.3-10.4V10.8C27.3 7.4 26.3 3 24 3z"/>
    <path fill="#e2e8f0" stroke="none" d="M24 3c2.3 0 3.3 4.4 3.3 7.8v24.8c0 5.2-1.6 10.4-3.3 10.4z"/>
    <path fill="#334155" stroke="none" d="m21.7 10 .5-2h3.6l.5 2-2.3-.6z"/>
    <path fill="currentColor" stroke="none" d="m24 34 1.4 8.5L24 47l-1.4-4.5z"/>
    <path d="M21.8 14v10m4.4-10v10" stroke="#64748b" stroke-width=".8" stroke-dasharray="1.2 1.5"/>
  </g>
</svg>`;

const SKINS = ["#e8b98e", "#a66d48", "#704831", "#f2cfad"];
const HAIR = ["#34271f", "#241f21", "#201b19", "#7b4a29"];

/** Side-profile supporters with full silhouettes, shoes and distinct outfits. */
export function fanSvg(variant: number, size: number): string {
  const v = Math.abs(Math.floor(variant));
  const pose = v % 4;
  const skin = SKINS[Math.floor(v / 4) % SKINS.length];
  const hair = HAIR[Math.floor(v / 4) % HAIR.length];
  const legs = `<g class="sd-leg-b"><path d="m14 24-1.5 10 2 2" fill="none" stroke="#475569" stroke-width="3.2" stroke-linecap="round"/><path d="M12.5 36h4" stroke="#e2e8f0" stroke-width="2" stroke-linecap="round"/></g>
    <g class="sd-leg-f"><path d="m17 24 1 10 2 2" fill="none" stroke="#1e293b" stroke-width="3.2" stroke-linecap="round"/><path d="M17 36h5" stroke="#f8fafc" stroke-width="2" stroke-linecap="round"/></g>`;
  const head = `<path d="M13 12v3h5v-4" fill="${skin}"/>
    <path d="M12 6.5q1-3.5 5-2.5 4 1 3 5l1.5 1-1.5 1q-.5 3-4 2-4-.5-4-6.5" fill="${skin}"/>
    <path d="M12 9q-2-6 3.5-6 5-.5 5 4l-5-1-1 4z" fill="${hair}"/>
    <circle cx="18.7" cy="8.7" r=".6" fill="#27272a"/>`;
  const cap = pose === 0 || pose === 3
    ? '<path d="M11 6q0-5 5-4 4 .5 4 4l4 1H11z" fill="currentColor"/><path d="M12 5h7" stroke="#fff" stroke-width=".7" opacity=".65"/>' : "";
  const jersey = `<path d="m12 14-3 4 3 2v5q4 1.5 8 0l-1-10-2-1q-2 3-5 0" fill="currentColor"/>
    <path d="m12 14 3 2 2-2M12 24h7" fill="none" stroke="#fff" stroke-width="1" opacity=".85"/>
    <path d="M12 17h2v4h-2m4-4h1v4" stroke="#fff" stroke-width=".7" fill="none" opacity=".8"/>`;
  const arm = `<g class="sd-arm"><path d="m18 16 1.5 4-1 4" fill="none" stroke="${skin}" stroke-width="2.5" stroke-linecap="round"/><path d="m18 16 1 2.5" stroke="currentColor" stroke-width="3.5"/></g>`;
  const scarf = `<path d="m12 16-4-6V5m10 11 5-6V5" fill="none" stroke="${skin}" stroke-width="2.2" stroke-linecap="round"/>
    <path d="M5 2h21v3H5zM5 4v3m21-3v3" fill="currentColor" stroke="currentColor" stroke-width="1"/>
    <path d="M9 2v3m5-3v3m5-3v3m4-3v3" stroke="#fff" stroke-width="1.5" opacity=".9"/>`;
  const flag = `<path d="m18 16 4-3 1-6" fill="none" stroke="${skin}" stroke-width="2.3" stroke-linecap="round"/>
    <path d="M23 15V1" stroke="#e2e8f0" stroke-width="1"/>
    <path class="sd-fan-flag" d="M23 1q4-2 8 1v7q-4-3-8-1z" fill="currentColor"/><path d="M24 3q3-1 6 1" stroke="#fff" stroke-width="1"/>`;
  const backpack = pose === 3 ? '<rect x="7" y="15" width="5" height="9" rx="2" fill="#94a3b8"/><path d="M8 21h3m0-6v8" stroke="#475569" stroke-width="1"/>' : "";
  return `<svg viewBox="0 0 32 40" width="${Math.round(size * .8)}" height="${size}" aria-hidden="true" focusable="false">
    <ellipse cx="16" cy="38" rx="7" ry="1.3" fill="#0f172a" opacity=".2"/>
    ${legs}${backpack}${jersey}${head}${cap}${pose === 1 ? scarf : pose === 2 ? flag : arm}
  </svg>`;
}
