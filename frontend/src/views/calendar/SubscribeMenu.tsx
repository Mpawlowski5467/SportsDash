import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { api } from "../../api";
import { Icon, TeamBlock } from "../../components/bs";
import { useIsActiveTab } from "../../components/NavigateContext";
import type { Team } from "../../types";

/**
 * `webcal://` subscription URL for the live calendar feed. `webcal://`
 * hands the URL to the OS calendar app (Apple/Google) which keeps polling
 * it, so subscribing stays live as games are added. Host comes from the
 * current page so it works behind the dev proxy and in production alike.
 */
function webcalUrl(teamId?: string): string {
  const base = `webcal://${window.location.host}/api/calendar.ics`;
  return teamId !== undefined
    ? `${base}?team_id=${encodeURIComponent(teamId)}`
    : base;
}

/** How long "Copied" stands in for "Copy" after a link is copied. */
const COPIED_MS = 1_500;

const ITEM =
  "bs-focus-inset flex min-h-11 cursor-pointer items-center gap-2.5 font-condensed text-[1.0625rem] font-bold uppercase leading-none tracking-[0.06em] text-ink hover:bg-hover";

/**
 * "Subscribe": a menu holding a live `webcal://` feed for everything
 * followed, one per followed team, and the one-off `.ics` download (which
 * used to be a button of its own beside this one). A feed's name opens it
 * in the OS calendar app; "Copy" beside it puts the link on the clipboard.
 *
 * `teams` arrives already cut to the follows a per-team feed can serve;
 * `omitsLeaderboardFollows` says whether anything was dropped, so the menu
 * can point those follows at the all-teams feed instead of silently
 * missing a row.
 *
 * Keyboard: Enter, Space or ArrowDown on the button open the menu on its
 * first item; the arrow keys move through the items (wrapping), Home and
 * End jump; Enter or Space act on an item; Escape closes and returns focus
 * to the button, wherever focus was; Tab closes.
 */
export default function SubscribeMenu({
  teams,
  omitsLeaderboardFollows,
}: {
  teams: readonly Team[];
  omitsLeaderboardFollows: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  // The menu hangs from whichever edge of the button keeps it on screen,
  // and never reaches past the window (see `Select`, which does the same).
  const [placement, setPlacement] = useState<{
    fromRight: boolean;
    reach: number | null;
  }>({ fromRight: true, reach: null });
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const noteId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    // Escape closes wherever focus is (after a click on the note above
    // the items it is on nothing): the menu's own handler only hears keys
    // pressed inside the list, and stops those from arriving here twice.
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // The view stays mounted while another tab shows (the kiosk rotation
  // moves on under it): a menu left open would still be hanging there when
  // the Calendar came round again.
  const showing = useIsActiveTab("calendar");
  useEffect(() => {
    if (!showing) setOpen(false);
  }, [showing]);

  // Opening moves focus to the first item, so the arrow keys work at once.
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  const show = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect !== undefined) {
      const viewport = document.documentElement.clientWidth;
      const fromRight = rect.left + rect.width / 2 > viewport / 2;
      setPlacement({
        fromRight,
        reach: fromRight ? rect.right : viewport - rect.left,
      });
    }
    setOpen(true);
  };

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  const copy = (key: string, url: string) => {
    // Clipboard API can be unavailable on insecure origins; swallow and
    // just skip the "Copied" flash rather than throwing.
    navigator.clipboard
      ?.writeText(url)
      .then(() => {
        setCopied(key);
        window.setTimeout(
          () => setCopied((prev) => (prev === key ? null : prev)),
          COPIED_MS,
        );
      })
      .catch(() => undefined);
  };

  const onTriggerKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show();
    }
  };

  const onMenuKeyDown = (event: KeyboardEvent) => {
    const items = [
      ...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ??
        []),
    ];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const go = (index: number) => {
      if (items.length === 0) return;
      event.preventDefault();
      items[(index + items.length) % items.length].focus();
    };
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        return;
      case "ArrowDown":
        go(at + 1);
        return;
      case "ArrowUp":
        go(at - 1);
        return;
      case "Home":
        go(0);
        return;
      case "End":
        go(items.length - 1);
        return;
      case "Tab":
        // Tabbing away closes rather than leaving a stranded open menu.
        close(false);
        return;
      case " ":
        // A menu item acts on Space. The Copy items are buttons and do so
        // by themselves; a link does not, and the page would scroll away
        // under the open menu instead.
        if (document.activeElement instanceof HTMLAnchorElement && at !== -1) {
          event.preventDefault();
          items[at].click();
        }
        return;
      default:
    }
  };

  const rows: { key: string; label: string; team?: Team }[] = [
    { key: "all", label: "All followed teams" },
    ...teams.map((team) => ({ key: team.id, label: team.name, team })),
  ];

  return (
    <div ref={containerRef} className="relative inline-block max-w-full">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        className="bs-btn bs-btn--solid pl-4"
      >
        <Icon>
          <path d="M4 6h16v14H4zM4 10h16M8 3v4M16 3v4M12 13v4M10 15h4" />
        </Icon>
        Subscribe
      </button>

      {open && (
        <div
          style={
            placement.reach === null
              ? undefined
              : { maxWidth: `calc(${placement.reach}px - 1rem)` }
          }
          className={
            "absolute z-30 mt-1 w-[22rem] max-w-[calc(100vw-2rem)] border-2 border-ink bg-sheet " +
            (placement.fromRight ? "right-0" : "left-0")
          }
        >
          <p
            id={noteId}
            className="m-0 border-b border-hair px-4 py-3 text-sm text-ink-2"
          >
            Subscribe in Apple or Google Calendar to keep games updating
            automatically.
            {omitsLeaderboardFollows
              ? " Tournaments belong to no team — the all-teams feed is the one that carries them."
              : ""}
          </p>
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label="Subscribe to calendar"
            aria-describedby={noteId}
            onKeyDown={onMenuKeyDown}
            className="max-h-[min(24rem,60vh)] overflow-y-auto"
          >
            {rows.map((row) => {
              const url = webcalUrl(row.team?.id);
              return (
                // `role="none"`: the row is layout; its link and its button
                // are the menu's items.
                <div
                  key={row.key}
                  role="none"
                  className="flex items-stretch border-b border-hair"
                >
                  <a
                    role="menuitem"
                    tabIndex={-1}
                    href={url}
                    title={`Subscribe: ${row.label}`}
                    className={`${ITEM} min-w-0 flex-1 no-underline ` + (row.team ? "pl-2 pr-3" : "px-4")}
                  >
                    {row.team && (
                      <TeamBlock
                        name={row.team.name}
                        abbreviation={row.team.abbreviation}
                        color={row.team.color}
                      />
                    )}
                    <span className="min-w-0 truncate">{row.label}</span>
                  </a>
                  <button
                    type="button"
                    role="menuitem"
                    tabIndex={-1}
                    onClick={() => copy(row.key, url)}
                    title="Copy subscription link"
                    aria-label={`Copy subscription link for ${row.label}`}
                    className={`${ITEM} w-[5.5rem] flex-none justify-center border-l border-hair`}
                  >
                    {copied === row.key ? "Copied" : "Copy"}
                  </button>
                </div>
              );
            })}
            <a
              role="menuitem"
              tabIndex={-1}
              href={api.calendarIcsUrl}
              download
              className={`${ITEM} px-4 no-underline`}
            >
              <Icon>
                <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />
              </Icon>
              Download .ics file
            </a>
          </div>
          {/* Said aloud when a link is copied; the button's own label does
              not change, so the flash would otherwise be silent. */}
          <p role="status" className="sr-only">
            {copied !== null ? "Link copied" : ""}
          </p>
        </div>
      )}
    </div>
  );
}
