// The rail down the window's left side (SPEC 14.1): ClipForge, then a link to each page there is so far,
// Today's with how many drafts wait for review, the Queue's with how many posts need you and Campaigns' with
// how many campaigns have a triangle.
// Under 900 px it keeps only the icons, with the names still there for screen readers.

import { useQuery } from "@tanstack/react-query";
import {
  BadgeDollarSign,
  CalendarCheck,
  FolderOpen,
  Library as LibraryIcon,
  ListChecks,
  type LucideIcon,
  Radio,
  Settings as SettingsIcon,
  Sparkles,
  TrendingUp,
  Users,
  Wallet,
} from "lucide-react";
import { Link, NavLink } from "react-router";

import { campaignsLabel } from "../lib/campaigns.ts";
import { todayLabel } from "../lib/drafts.ts";
import { queueLabel } from "../lib/queue.ts";
import {
  campaignCountQuery,
  draftCountQuery,
  needsYouQuery,
} from "../state.ts";

type Page = { to: string; label: string; icon: LucideIcon };

// Each page adds its own link as it's built, so the rail never offers a page that isn't there.
const PAGES: Page[] = [
  { to: "/", label: "Today", icon: CalendarCheck },
  { to: "/create", label: "Create", icon: Sparkles },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/queue", label: "Queue", icon: ListChecks },
  { to: "/channels", label: "Channels", icon: Radio },
  { to: "/campaigns", label: "Campaigns", icon: BadgeDollarSign },
  { to: "/money", label: "Money", icon: Wallet },
  { to: "/accounts", label: "Accounts", icon: Users },
  { to: "/library", label: "Library", icon: LibraryIcon },
  { to: "/trends", label: "Trends", icon: TrendingUp },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

const LABEL = "truncate max-[900px]:sr-only";
// Under 900 px, where the labels are hidden, the page's name beside its icon while the pointer or the
// keyboard is on it. Screen readers have the label already.
const TIP =
  "pointer-events-none absolute left-full top-1/2 z-50 ml-2 hidden -translate-y-1/2 whitespace-nowrap " +
  "rounded-control border border-line bg-panel px-2 py-1 text-xs text-fg shadow-md " +
  "max-[900px]:group-hover:block max-[900px]:group-focus-visible:block";

export function Rail() {
  const needsYou = useQuery(needsYouQuery).data ?? 0;
  const drafts = useQuery(draftCountQuery).data ?? 0;
  const campaigns = useQuery(campaignCountQuery).data ?? 0;
  const counts: Record<
    string,
    { n: number; label: string; tone: string } | undefined
  > = {
    "/":
      drafts > 0
        ? { n: drafts, label: todayLabel(drafts), tone: "border-accent" }
        : undefined,
    "/queue":
      needsYou > 0
        ? { n: needsYou, label: queueLabel(needsYou), tone: "border-warn" }
        : undefined,
    "/campaigns":
      campaigns > 0
        ? {
            n: campaigns,
            label: campaignsLabel(campaigns),
            tone: "border-warn",
          }
        : undefined,
  };
  return (
    <nav
      aria-label="Pages"
      className={
        "flex w-52 shrink-0 flex-col gap-1 border-r border-line bg-panel p-3 " +
        "max-[900px]:w-16 max-[900px]:items-center max-[900px]:px-2"
      }
    >
      <Link
        to="/"
        className="mb-3 flex items-center gap-2 rounded-control px-2 py-1.5 text-base font-semibold"
      >
        <Logo />
        <span className={LABEL}>ClipForge</span>
      </Link>
      {PAGES.length > 0 && (
        <ul className="flex flex-col gap-1">
          {PAGES.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={to === "/"}
                aria-label={counts[to]?.label}
                className={({ isActive }) =>
                  "group relative flex items-center gap-3 rounded-control px-2 py-1.5 text-sm " +
                  (isActive
                    ? "bg-panel-2 text-fg"
                    : "text-muted hover:bg-panel-2 hover:text-fg")
                }
              >
                <Icon aria-hidden size={18} className="shrink-0" />
                <span className={LABEL}>{label}</span>
                <span aria-hidden data-tip className={TIP}>
                  {label}
                </span>
                {counts[to] && (
                  <Count n={counts[to].n} tone={counts[to].tone} />
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

// A count beside the name (drafts to review, posts or campaigns that need you), or on the icon's corner when
// the rail is narrow; its border's colour says which.
function Count({ n, tone }: { n: number; tone: string }) {
  return (
    <span
      aria-hidden
      className={
        `ml-auto min-w-5 rounded-full border ${tone} bg-panel px-1.5 text-center text-xs font-semibold text-fg tabular-nums ` +
        "max-[900px]:absolute max-[900px]:-top-1 max-[900px]:-right-1 max-[900px]:ml-0"
      }
    >
      {n}
    </span>
  );
}

function Logo() {
  return (
    <svg aria-hidden viewBox="0 0 32 32" className="size-6 shrink-0">
      <rect width="32" height="32" rx="8" fill="#8B5CF6" />
      <path d="M13 10.5v11l9-5.5z" fill="#fff" />
    </svg>
  );
}
