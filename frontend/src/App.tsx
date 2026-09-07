import { useState, useCallback, useRef } from "react";
import { useEffect } from "react";

// ─── Types ────────────────────────────────────────────────────────────────────

type Theme = "dark" | "light";
type ViewMode = "list" | "grid";
type GitStatus = "M" | "A" | "D" | "R" | "?" | "staged" | "clean";

interface FileItem {
  id: string;
  name: string;
  type: "folder" | "file";
  ext?: string;
  size?: string;
  modified: string;
  gitStatus?: GitStatus;
}

interface SidebarLocation {
  id: string;
  label: string;
  path: string;
  icon: string;
}

interface GitChange {
  path: string;
  status: GitStatus;
  staged: boolean;
}

// ─── Mock Data ─────────────────────────────────────────────────────────────────

const MOCK_FILES: FileItem[] = [
  { id: "1", name: "src", type: "folder", modified: "2 min ago", gitStatus: "M" },
  { id: "2", name: "tests", type: "folder", modified: "1 hr ago" },
  { id: "3", name: "docs", type: "folder", modified: "3 days ago" },
  { id: "4", name: "node_modules", type: "folder", modified: "5 days ago" },
  { id: "5", name: "README.md", type: "file", ext: "md", size: "3.2 KB", modified: "2 min ago", gitStatus: "M" },
  { id: "6", name: "package.json", type: "file", ext: "json", size: "1.1 KB", modified: "1 hr ago", gitStatus: "staged" },
  { id: "7", name: "tsconfig.json", type: "file", ext: "json", size: "512 B", modified: "3 days ago" },
  { id: "8", name: "vite.config.ts", type: "file", ext: "ts", size: "890 B", modified: "5 days ago" },
  { id: "9", name: ".gitignore", type: "file", ext: "gitignore", size: "280 B", modified: "2 wks ago" },
  { id: "10", name: "index.html", type: "file", ext: "html", size: "640 B", modified: "5 days ago" },
  { id: "11", name: "CHANGELOG.md", type: "file", ext: "md", size: "12 KB", modified: "6 days ago" },
  { id: "12", name: "LICENSE", type: "file", size: "1.1 KB", modified: "2 mos ago" },
  { id: "13", name: ".env.example", type: "file", ext: "env", size: "180 B", modified: "1 wk ago", gitStatus: "A" },
  { id: "14", name: "docker-compose.yml", type: "file", ext: "yml", size: "420 B", modified: "4 days ago", gitStatus: "?" },
];

const MOCK_CHANGES: GitChange[] = [
  { path: "src/components/FileTree.tsx", status: "M", staged: false },
  { path: "src/services/git.ts", status: "M", staged: false },
  { path: "README.md", status: "M", staged: false },
  { path: ".env.example", status: "A", staged: false },
  { path: "docker-compose.yml", status: "?", staged: false },
  { path: "package.json", status: "M", staged: true },
  { path: "src/index.css", status: "M", staged: true },
];

const SIDEBAR_LOCATIONS: SidebarLocation[] = [
  { id: "home", label: "Home", path: "~", icon: "home" },
  { id: "desktop", label: "Desktop", path: "~/Desktop", icon: "desktop" },
  { id: "downloads", label: "Downloads", path: "~/Downloads", icon: "download" },
  { id: "documents", label: "Documents", path: "~/Documents", icon: "doc" },
];

const RECENT_REPOS = [
  { name: "visual-git-app", branch: "main", path: "~/projects/visual-git-app", dirty: true },
  { name: "portfolio-v3", branch: "feat/dark-mode", path: "~/projects/portfolio-v3", dirty: false },
  { name: "api-gateway", branch: "main", path: "~/work/api-gateway", dirty: false },
];

// ─── Icons ────────────────────────────────────────────────────────────────────

function FolderIcon({ color = "currentColor" }: { color?: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M2 5a2 2 0 012-2h3l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V5z" fill={color} fillOpacity="0.15" stroke={color} strokeWidth="1.2" />
    </svg>
  );
}

function FileIcon({ ext = "" }: { ext?: string }) {
  const extColors: Record<string, string> = {
    ts: "#3b82f6", tsx: "#06b6d4", js: "#f59e0b", jsx: "#06b6d4",
    json: "#10b981", md: "#8b5cf6", html: "#f97316", css: "#ec4899",
    yml: "#f59e0b", yaml: "#f59e0b", env: "#16a34a", gitignore: "#6b7280",
  };
  const color = extColors[ext] ?? "var(--text-faint)";
  return (
    <svg width="16" height="18" viewBox="0 0 16 18" fill="none">
      <path d="M2 2a1 1 0 011-1h8l3 3v12a1 1 0 01-1 1H3a1 1 0 01-1-1V2z" fill={color} fillOpacity="0.12" stroke={color} strokeWidth="1.2" />
      <path d="M10 1v3h3" stroke={color} strokeWidth="1.2" strokeLinecap="round" />
      {ext && (
        <text x="8" y="13.5" textAnchor="middle" fontSize="4.5" fontFamily="JetBrains Mono, monospace" fontWeight="500" fill={color}>
          {ext.toUpperCase().slice(0, 3)}
        </text>
      )}
    </svg>
  );
}

function GitDot({ status }: { status: GitStatus }) {
  const map: Record<GitStatus, { label: string; color: string }> = {
    M: { label: "M", color: "var(--git-modified)" },
    A: { label: "A", color: "var(--git-added)" },
    D: { label: "D", color: "var(--git-deleted)" },
    R: { label: "R", color: "var(--git-renamed)" },
    "?": { label: "?", color: "var(--git-untracked)" },
    staged: { label: "S", color: "var(--git-staged)" },
    clean: { label: "", color: "transparent" },
  };
  const { label, color } = map[status] ?? map.clean;
  if (!label) return null;
  return (
    <span style={{
      fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 500,
      color, background: color + "18", border: `1px solid ${color}40`,
      borderRadius: 3, padding: "1px 4px", letterSpacing: "0.02em",
      lineHeight: 1, display: "inline-flex", alignItems: "center",
    }}>
      {label}
    </span>
  );
}

// ─── GitFinder Logo ───────────────────────────────────────────────────────────

function GitFinderLogo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      {/* Folder body */}
      <rect x="4" y="13" width="24" height="14" rx="2.5" fill="var(--accent-fg)" fillOpacity="0.18" stroke="var(--accent-fg)" strokeWidth="1.4" />
      {/* Folder tab */}
      <path d="M4 13v-2.5A2 2 0 016 8.5h5.5l2 2H26" stroke="var(--accent-fg)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      {/* Git commit node - center */}
      <circle cx="16" cy="20" r="2.5" fill="var(--accent-fg)" />
      {/* Branch lines */}
      <line x1="9" y1="20" x2="13.5" y2="20" stroke="var(--accent-fg)" strokeWidth="1.4" strokeLinecap="round" />
      <line x1="18.5" y1="20" x2="23" y2="20" stroke="var(--accent-fg)" strokeWidth="1.4" strokeLinecap="round" />
      {/* Branch dot left */}
      <circle cx="8" cy="20" r="1.5" fill="var(--accent-fg)" fillOpacity="0.55" />
      {/* Branch dot right */}
      <circle cx="24" cy="20" r="1.5" fill="var(--accent-fg)" fillOpacity="0.55" />
      {/* Branch arm going up-right */}
      <path d="M22.5 20v-3.5" stroke="var(--accent-fg)" strokeWidth="1.3" strokeLinecap="round" opacity="0.6" />
      <circle cx="22.5" cy="15.5" r="1.2" fill="var(--accent-fg)" fillOpacity="0.5" />
    </svg>
  );
}

// ─── Animated Icon Button ─────────────────────────────────────────────────────

function IconBtn({
  children, onClick, title, active, className = "",
}: {
  children: React.ReactNode; onClick?: () => void; title?: string; active?: boolean; className?: string;
}) {
  const [bouncing, setBouncing] = useState(false);
  const [ripple, setRipple] = useState(false);

  const handleClick = () => {
    setBouncing(false); setRipple(false);
    requestAnimationFrame(() => {
      setBouncing(true); setRipple(true);
      setTimeout(() => { setBouncing(false); setRipple(false); }, 400);
    });
    onClick?.();
  };

  return (
    <button title={title} onClick={handleClick} className={className}
      style={{
        position: "relative", overflow: "hidden", display: "inline-flex",
        alignItems: "center", justifyContent: "center", gap: 6,
        padding: "6px 10px", borderRadius: 6,
        border: `1px solid ${active ? "var(--accent)" : "transparent"}`,
        background: active ? "var(--accent)" : "transparent",
        color: active ? "var(--accent-fg)" : "var(--text-muted)",
        cursor: "pointer", fontSize: 13, fontFamily: "var(--font-ui)", fontWeight: 500,
        transition: "background 0.15s, color 0.15s, border-color 0.15s", whiteSpace: "nowrap",
      }}
      onMouseEnter={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = "var(--border-subtle)"; (e.currentTarget as HTMLElement).style.color = "var(--text)"; } }}
      onMouseLeave={e => { if (!active) { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; } }}
    >
      <span className={bouncing ? "icon-bounce" : ""} style={{ display: "inline-flex", alignItems: "center" }}>
        {children}
      </span>
      {ripple && <span style={{ position: "absolute", inset: 0, borderRadius: 6, background: "var(--accent)", animation: "rippleOut 0.4s ease-out forwards", pointerEvents: "none", opacity: 0.15 }} />}
    </button>
  );
}

// ─── Toolbar Button ───────────────────────────────────────────────────────────

function ToolBtn({ icon, label, onClick, accent, disabled }: {
  icon: React.ReactNode; label: string; onClick?: () => void; accent?: boolean; disabled?: boolean;
}) {
  const [bouncing, setBouncing] = useState(false);
  const handleClick = () => {
    if (disabled) return;
    setBouncing(false);
    requestAnimationFrame(() => { setBouncing(true); setTimeout(() => setBouncing(false), 300); });
    onClick?.();
  };
  return (
    <button onClick={handleClick}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
        padding: "7px 10px", borderRadius: 7,
        border: accent ? "1px solid var(--accent)" : "1px solid transparent",
        background: accent ? "var(--accent)" : "transparent",
        color: disabled ? "var(--text-faint)" : (accent ? "var(--accent-fg)" : "var(--text-muted)"),
        cursor: disabled ? "not-allowed" : "pointer", fontSize: 10,
        fontFamily: "var(--font-mono)", fontWeight: 500, letterSpacing: "0.03em",
        textTransform: "uppercase", transition: "all 0.15s", minWidth: 52, opacity: disabled ? 0.45 : 1,
      }}
      onMouseEnter={e => { if (!accent && !disabled) { (e.currentTarget as HTMLElement).style.background = "var(--border-subtle)"; (e.currentTarget as HTMLElement).style.color = "var(--text)"; } }}
      onMouseLeave={e => { if (!accent && !disabled) { (e.currentTarget as HTMLElement).style.background = "transparent"; (e.currentTarget as HTMLElement).style.color = "var(--text-muted)"; } }}
    >
      <span className={bouncing ? "icon-bounce" : ""} style={{ display: "flex" }}>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

// ─── Divider ──────────────────────────────────────────────────────────────────

function ToolDivider() {
  return <div style={{ width: 1, height: 32, background: "var(--border)", margin: "0 4px", alignSelf: "center" }} />;
}

// ─── SVG Icon Set ─────────────────────────────────────────────────────────────

const I = {
  // Theme — moon = dark mode active, sun = light mode active
  sun: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.4" />
      {[0,45,90,135,180,225,270,315].map((deg, i) => {
        const r = Math.PI * deg / 180;
        const x1 = 8 + 4.8 * Math.cos(r), y1 = 8 + 4.8 * Math.sin(r);
        const x2 = 8 + 6.2 * Math.cos(r), y2 = 8 + 6.2 * Math.sin(r);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />;
      })}
    </svg>
  ),
  moon: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M13 9.5A5.5 5.5 0 117 3.5a4.5 4.5 0 006 6z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  ),
  list: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><line x1="3" y1="4" x2="13" y2="4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><line x1="3" y1="8" x2="13" y2="8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><line x1="3" y1="12" x2="13" y2="12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  grid: <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4"/><rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4"/><rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4"/><rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4"/></svg>,
  // Up arrow — clean chevron upward with tail
  up: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 13V4M4.5 7.5L8 4l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  // Refresh — classic circular arrows (↺)
  refresh: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M13.5 8A5.5 5.5 0 113.2 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
      <path d="M3 2v3h3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  // History — vertical commit log with nodes
  history: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="4" cy="3" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="4" cy="7" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="4" cy="11" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="4" y1="4.5" x2="4" y2="5.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="4" y1="8.5" x2="4" y2="9.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="3" x2="12" y2="3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="7" x2="12" y2="7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="11" x2="12" y2="11" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  // Merge — two branches converging (Y shape, upside down)
  merge: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="4" cy="3" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="10" cy="3" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="7" cy="12" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M4 4.5C4 8 7 9.5 7 10.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <path d="M10 4.5C10 8 7 9.5 7 10.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  plus: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  trash: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 4h10M5 4V2.5h4V4M4 4l.5 8h5L10 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  copy: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="5" y="5" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.3"/><path d="M3 9H2.5A1.5 1.5 0 011 7.5v-5A1.5 1.5 0 012.5 1h5A1.5 1.5 0 019 2.5V3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  cut: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="3.5" cy="11" r="2" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="10.5" cy="11" r="2" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="3.5" y1="9" x2="12" y2="2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="10.5" y1="9" x2="2" y2="2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  paste: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="3" y="4" width="9" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M5 4V3a1 1 0 011-1h3a1 1 0 011 1v1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="5" y1="7.5" x2="9" y2="7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="5" y1="9.5" x2="9" y2="9.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  rename: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M2 10.5l1-4L8.5 1l3 3L6 9.5l-4 1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
      <line x1="11" y1="12" x2="3" y2="12" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  openFolder: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M1 4a1 1 0 011-1h3l1.5 1.5H12a1 1 0 011 1v5a1 1 0 01-1 1H2a1 1 0 01-1-1V4z" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M5.5 8.5l2-1.5 2 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
      <line x1="7.5" y1="7" x2="7.5" y2="10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  gitBranch: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="4" cy="3" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="4" cy="11" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="10" cy="5" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="4" y1="4.5" x2="4" y2="9.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <path d="M4 6C4 8 10 7 10 6.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  stage: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 10V3M4 6l3-3 3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/><line x1="2" y1="11.5" x2="12" y2="11.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  stageAll: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M4.5 9V4M2.5 6l2-2 2 2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M9.5 9V4M7.5 6l2-2 2 2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <line x1="1.5" y1="11.5" x2="12.5" y2="11.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
    </svg>
  ),
  unstage: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 4v7M4 8l3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/><line x1="2" y1="2.5" x2="12" y2="2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  commit: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="2.5" stroke="currentColor" strokeWidth="1.3"/><line x1="1" y1="7" x2="4.5" y2="7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><line x1="9.5" y1="7" x2="13" y2="7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  // Graph — dot grid connected (DAG-style)
  graph: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="3" cy="11" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="7" cy="7" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="11" cy="3" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <circle cx="11" cy="7" r="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <line x1="4.1" y1="10.1" x2="5.9" y2="7.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="8.1" y1="6.1" x2="9.9" y2="3.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="8.5" y1="7" x2="9.5" y2="7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  ),
  conflicts: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M7 1.5L13 12.5H1L7 1.5z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
      <line x1="7" y1="6" x2="7" y2="9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
      <circle cx="7" cy="10.5" r="0.9" fill="currentColor"/>
    </svg>
  ),
  chevRight: <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4.5 2.5L7.5 6l-3 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  home: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 6.5L7 2l5 4.5V12H9V9H5v3H2V6.5z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/></svg>,
  desktop: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1" y="2" width="12" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.2"/><path d="M5 13h4M7 10v3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>,
  download: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4.5 6.5L7 9l2.5-2.5M2 11h10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  doc: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 1.5A1.5 1.5 0 014.5 0h4L11 2.5V12.5A1.5 1.5 0 019.5 14h-5A1.5 1.5 0 013 12.5V1.5z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/><path d="M8 0v3h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>,
  search: <svg width="15" height="15" viewBox="0 0 15 15" fill="none"><circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" strokeWidth="1.3"/><path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  check: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 7l3.5 3.5L12 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>,
};

const sidebarIconMap: Record<string, React.ReactNode> = {
  home: I.home, desktop: I.desktop, download: I.download, doc: I.doc,
};

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [theme, setTheme] = useState<Theme>("dark");
  const [view, setView] = useState<ViewMode>("list");
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [selectedLocation, setSelectedLocation] = useState("home");
  const [changes, setChanges] = useState<GitChange[]>(MOCK_CHANGES);
  const [commitMsg, setCommitMsg] = useState("");
  const [commitDone, setCommitDone] = useState(false);
  const [gitPanelOpen, setGitPanelOpen] = useState(true);
  const [searchFocused, setSearchFocused] = useState(false);
  const [currentPath] = useState(["~", "projects", "visual-git-app"]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const staged = changes.filter(c => c.staged);
  const unstaged = changes.filter(c => !c.staged);

  const toggleStage = useCallback((path: string) => {
    setChanges(prev => prev.map(c => c.path === path ? { ...c, staged: !c.staged } : c));
  }, []);

  const stageAll = useCallback(() => {
    setChanges(prev => prev.map(c => ({ ...c, staged: true })));
  }, []);

  const handleCommit = useCallback(() => {
    if (!commitMsg.trim() || staged.length === 0) return;
    setChanges(prev => prev.filter(c => !c.staged));
    setCommitMsg("");
    setCommitDone(true);
    setTimeout(() => setCommitDone(false), 2200);
  }, [commitMsg, staged]);

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    const next = new Set(selectedFiles);
    if (e.metaKey || e.ctrlKey) { next.has(id) ? next.delete(id) : next.add(id); }
    else if (e.shiftKey) { next.add(id); }
    else { next.clear(); next.add(id); }
    setSelectedFiles(next);
  };

  return (
    <div style={{ fontFamily: "var(--font-ui)", background: "var(--bg)", color: "var(--text)", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* ── Top Bar ─────────────────────────────────────────────────── */}
      <header style={{
        height: "var(--topbar-h)", background: "var(--surface)",
        borderBottom: "1px solid var(--border)", display: "flex",
        alignItems: "center", padding: "0 14px", gap: 10, flexShrink: 0, zIndex: 10,
      }}>
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginRight: 6, flexShrink: 0 }}>
          <GitFinderLogo size={26} />
          <span style={{ fontWeight: 600, fontSize: 14.5, letterSpacing: "-0.02em", color: "var(--text)" }}>GitFinder</span>
        </div>

        {/* Nav */}
        <IconBtn onClick={() => {}} title="Parent folder">{I.up}</IconBtn>
        <IconBtn onClick={() => {}} title="Refresh">{I.refresh}</IconBtn>

        {/* Breadcrumb */}
        <div style={{ display: "flex", alignItems: "center", gap: 3, flex: 1, overflow: "hidden", minWidth: 0 }}>
          {currentPath.map((seg, i) => (
            <span key={i} style={{ display: "flex", alignItems: "center", gap: 3, flexShrink: i < currentPath.length - 1 ? 0 : 1 }}>
              {i > 0 && <span style={{ color: "var(--text-faint)", display: "flex" }}>{I.chevRight}</span>}
              <button style={{
                fontFamily: "var(--font-mono)", fontSize: 12,
                color: i === currentPath.length - 1 ? "var(--text)" : "var(--text-muted)",
                fontWeight: i === currentPath.length - 1 ? 500 : 400,
                background: "none", border: "none", cursor: "pointer",
                padding: "2px 5px", borderRadius: 4, transition: "background 0.12s", whiteSpace: "nowrap",
              }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--border-subtle)")}
              onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
              >{seg}</button>
            </span>
          ))}
        </div>

        {/* Search */}
        <div style={{
          display: "flex", alignItems: "center", gap: 7,
          background: "var(--bg)", border: `1px solid ${searchFocused ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8, padding: "5px 10px", width: 190, transition: "border-color 0.15s", flexShrink: 0,
        }}>
          <span style={{ color: "var(--text-faint)", display: "flex" }}>{I.search}</span>
          <input onFocus={() => setSearchFocused(true)} onBlur={() => setSearchFocused(false)}
            placeholder="Search files…"
            style={{ background: "none", border: "none", outline: "none", fontSize: 13, color: "var(--text)", fontFamily: "var(--font-ui)", width: "100%" }}
          />
        </div>

        {/* View Toggle */}
        <div style={{ display: "flex", background: "var(--bg)", borderRadius: 7, padding: 2, border: "1px solid var(--border)", gap: 2, flexShrink: 0 }}>
          <IconBtn onClick={() => setView("list")} active={view === "list"} title="List view">{I.list}</IconBtn>
          <IconBtn onClick={() => setView("grid")} active={view === "grid"} title="Grid view">{I.grid}</IconBtn>
        </div>

        {/* Theme toggle — shows current mode icon */}
        <IconBtn onClick={() => setTheme(t => t === "dark" ? "light" : "dark")} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
          {theme === "dark" ? I.moon : I.sun}
        </IconBtn>

        {/* Branch pill */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6, padding: "5px 11px",
          borderRadius: 7, background: "var(--accent)", color: "var(--accent-fg)",
          fontSize: 12, fontFamily: "var(--font-mono)", fontWeight: 500, flexShrink: 0,
        }}>
          {I.gitBranch}
          <span>main</span>
          {changes.length > 0 && (
            <span style={{
              background: "var(--accent-fg)", color: "var(--accent)",
              borderRadius: "50%", width: 15, height: 15,
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              fontSize: 9, fontWeight: 700,
            }}>{changes.length}</span>
          )}
        </div>
      </header>

      {/* ── Body ────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* ── Sidebar ─────────────────────────────────────────────── */}
        <aside style={{
          width: "var(--sidebar-w)", background: "var(--surface)",
          borderRight: "1px solid var(--border)", display: "flex",
          flexDirection: "column", flexShrink: 0, overflow: "hidden",
        }}>
          <div style={{ flex: 1, overflowY: "auto", padding: "14px 0" }}>
            <SidebarSection label="Locations">
              {SIDEBAR_LOCATIONS.map(loc => (
                <SidebarBtn key={loc.id} icon={sidebarIconMap[loc.icon]} label={loc.label}
                  active={selectedLocation === loc.id}
                  onClick={() => setSelectedLocation(loc.id)}
                />
              ))}
            </SidebarSection>

            <div style={{ height: 1, background: "var(--border)", margin: "8px 12px" }} />

            <SidebarSection label="Recent Repos">
              {RECENT_REPOS.map(repo => (
                <button key={repo.name}
                  style={{ width: "100%", display: "flex", flexDirection: "column", gap: 3, padding: "7px 16px", background: "transparent", border: "none", borderLeft: "2px solid transparent", cursor: "pointer", textAlign: "left", transition: "background 0.12s" }}
                  onMouseEnter={e => (e.currentTarget.style.background = "var(--border-subtle)")}
                  onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
                    <span style={{ color: "var(--text-faint)" }}>{I.gitBranch}</span>
                    <span style={{ fontSize: 12.5, color: "var(--text)", fontWeight: 500 }}>{repo.name}</span>
                    {repo.dirty && <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--git-modified)", flexShrink: 0, marginLeft: "auto" }} />}
                  </div>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-faint)", paddingLeft: 20 }}>{repo.branch}</span>
                </button>
              ))}
            </SidebarSection>
          </div>

          {/* User */}
          <div style={{ borderTop: "1px solid var(--border)", padding: "10px 14px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <div style={{
                width: 28, height: 28, borderRadius: "50%",
                background: "var(--accent)", color: "var(--accent-fg)",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, fontWeight: 700, flexShrink: 0,
              }}>M</div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600 }}>mattyou7</div>
                <div style={{ fontSize: 10, color: "var(--text-faint)", fontFamily: "var(--font-mono)" }}>github.com</div>
              </div>
            </div>
          </div>
        </aside>

        {/* ── Main Content ─────────────────────────────────────────── */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Toolbar */}
          <div style={{
            background: "var(--surface)", borderBottom: "1px solid var(--border)",
            padding: "3px 12px", display: "flex", alignItems: "center", gap: 0, flexShrink: 0, overflowX: "auto",
          }}>
            {/* Filesystem group */}
            <ToolBtn icon={I.plus} label="New" />
            <ToolBtn icon={I.openFolder} label="Open" />
            <ToolBtn icon={I.copy} label="Copy" />
            <ToolBtn icon={I.cut} label="Cut" />
            <ToolBtn icon={I.paste} label="Paste" />
            <ToolBtn icon={I.rename} label="Rename" />
            <ToolBtn icon={I.trash} label="Delete" />
            <ToolDivider />
            {/* Git group */}
            <ToolBtn icon={I.stage} label="Stage" />
            <ToolBtn icon={I.stageAll} label="Stage All" onClick={stageAll} />
            <ToolBtn icon={I.unstage} label="Unstage" />
            <ToolDivider />
            <ToolBtn icon={I.history} label="History" />
            <ToolBtn icon={I.gitBranch} label="Branch" />
            <ToolBtn icon={I.graph} label="Graph" />
            <ToolBtn icon={I.merge} label="Merge" />
            <ToolBtn icon={I.conflicts} label="Conflicts" />
            <div style={{ marginLeft: "auto" }}>
              <ToolBtn icon={I.commit} label="Commit"
                accent={staged.length > 0 && commitMsg.trim().length > 0}
                onClick={handleCommit}
              />
            </div>
          </div>

          {/* File Area */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {view === "list"
              ? <ListView files={MOCK_FILES} selectedFiles={selectedFiles} toggleSelect={toggleSelect} />
              : <GridView files={MOCK_FILES} selectedFiles={selectedFiles} toggleSelect={toggleSelect} />
            }
          </div>

          {/* Git panel toggle */}
          <button onClick={() => setGitPanelOpen(o => !o)}
            style={{
              flexShrink: 0, display: "flex", alignItems: "center", gap: 8,
              padding: "5px 16px", background: "var(--surface)", border: "none",
              borderTop: "1px solid var(--border)", color: "var(--text-muted)",
              cursor: "pointer", fontSize: 10, fontFamily: "var(--font-mono)",
              textTransform: "uppercase", letterSpacing: "0.06em", transition: "background 0.12s",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "var(--surface-raised)")}
            onMouseLeave={e => (e.currentTarget.style.background = "var(--surface)")}
          >
            <span style={{ display: "flex", transform: gitPanelOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </span>
            Changes
            <span style={{
              marginLeft: 4, padding: "1px 6px", borderRadius: 10, fontSize: 10, fontWeight: 600,
              background: staged.length > 0 ? "var(--accent)" : "var(--border)",
              color: staged.length > 0 ? "var(--accent-fg)" : "var(--text-muted)",
            }}>{staged.length}/{changes.length}</span>
          </button>

          {/* Git Panel */}
          {gitPanelOpen && (
            <GitPanel staged={staged} unstaged={unstaged}
              commitMsg={commitMsg} setCommitMsg={setCommitMsg}
              onCommit={handleCommit} onToggleStage={toggleStage}
              onStageAll={stageAll} commitDone={commitDone}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Sidebar helpers ──────────────────────────────────────────────────────────

function SidebarSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ padding: "0 14px 5px", fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
        {label}
      </div>
      {children}
    </div>
  );
}

function SidebarBtn({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      style={{
        width: "100%", display: "flex", alignItems: "center", gap: 10,
        padding: "6px 16px", background: active ? "var(--accent)16" : "transparent",
        border: "none", borderLeft: `2px solid ${active ? "var(--accent)" : "transparent"}`,
        color: active ? "var(--text)" : "var(--text-muted)", cursor: "pointer",
        fontSize: 12.5, fontFamily: "var(--font-ui)", textAlign: "left", transition: "all 0.12s",
      }}
      onMouseEnter={e => { if (!active) (e.currentTarget as HTMLElement).style.background = "var(--border-subtle)"; }}
      onMouseLeave={e => { if (!active) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
    >
      <span style={{ color: active ? "var(--accent)" : "var(--text-faint)" }}>{icon}</span>
      {label}
    </button>
  );
}

// ─── List View ─────────────────────────────────────────────────────────────────

function ListView({ files, selectedFiles, toggleSelect }: {
  files: FileItem[]; selectedFiles: Set<string>; toggleSelect: (id: string, e: React.MouseEvent) => void;
}) {
  return (
    <div style={{ flex: 1, overflowY: "auto" }}>
      <div style={{
        display: "grid", gridTemplateColumns: "32px 1fr 80px 110px 52px",
        padding: "5px 16px", borderBottom: "1px solid var(--border)",
        position: "sticky", top: 0, background: "var(--surface)", zIndex: 1,
      }}>
        {["", "Name", "Size", "Modified", "Git"].map((h, i) => (
          <span key={i} style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", letterSpacing: "0.06em", textTransform: "uppercase", userSelect: "none" }}>
            {h}
          </span>
        ))}
      </div>
      {files.map((f, idx) => {
        const selected = selectedFiles.has(f.id);
        return (
          <div key={f.id} className="row-in" onClick={e => toggleSelect(f.id, e)}
            style={{
              display: "grid", gridTemplateColumns: "32px 1fr 80px 110px 52px",
              padding: "5px 16px", alignItems: "center", cursor: "pointer",
              background: selected ? "var(--accent)12" : "transparent",
              borderLeft: `2px solid ${selected ? "var(--accent)" : "transparent"}`,
              borderBottom: "1px solid var(--border-subtle)",
              transition: "background 0.1s", animationDelay: `${idx * 18}ms`, animationFillMode: "both",
            }}
            onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = "var(--border-subtle)"; }}
            onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
          >
            <span>{f.type === "folder" ? <FolderIcon color={selected ? "var(--accent)" : "var(--text-faint)"} /> : <FileIcon ext={f.ext} />}</span>
            <span style={{ fontSize: 13, fontWeight: f.type === "folder" ? 500 : 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-faint)" }}>{f.size ?? "—"}</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-faint)" }}>{f.modified}</span>
            <span>{f.gitStatus && f.gitStatus !== "clean" && <GitDot status={f.gitStatus} />}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Grid View ─────────────────────────────────────────────────────────────────

function GridView({ files, selectedFiles, toggleSelect }: {
  files: FileItem[]; selectedFiles: Set<string>; toggleSelect: (id: string, e: React.MouseEvent) => void;
}) {
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(115px, 1fr))", gap: 9 }}>
        {files.map((f, idx) => {
          const selected = selectedFiles.has(f.id);
          return (
            <div key={f.id} className="row-in" onClick={e => toggleSelect(f.id, e)}
              style={{
                display: "flex", flexDirection: "column", alignItems: "center", gap: 8,
                padding: "14px 10px 10px", borderRadius: 10,
                background: selected ? "var(--accent)12" : "var(--surface)",
                border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer", transition: "all 0.15s",
                animationDelay: `${idx * 14}ms`, animationFillMode: "both", position: "relative",
              }}
              onMouseEnter={e => { if (!selected) { (e.currentTarget as HTMLElement).style.background = "var(--surface-raised)"; (e.currentTarget as HTMLElement).style.borderColor = "var(--text-faint)"; } }}
              onMouseLeave={e => { if (!selected) { (e.currentTarget as HTMLElement).style.background = "var(--surface)"; (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; } }}
            >
              <span style={{ transform: "scale(1.5)", display: "flex", marginBottom: 2 }}>
                {f.type === "folder" ? <FolderIcon color={selected ? "var(--accent)" : "var(--text-faint)"} /> : <FileIcon ext={f.ext} />}
              </span>
              <span style={{ fontSize: 11, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", width: "100%", fontWeight: f.type === "folder" ? 500 : 400 }}>{f.name}</span>
              {f.gitStatus && f.gitStatus !== "clean" && (
                <div style={{ position: "absolute", top: 6, right: 6 }}><GitDot status={f.gitStatus} /></div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Git Panel ─────────────────────────────────────────────────────────────────

function GitPanel({ staged, unstaged, commitMsg, setCommitMsg, onCommit, onToggleStage, onStageAll, commitDone }: {
  staged: GitChange[]; unstaged: GitChange[]; commitMsg: string;
  setCommitMsg: (s: string) => void; onCommit: () => void;
  onToggleStage: (path: string) => void; onStageAll: () => void; commitDone: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const statusColor = (s: GitStatus) => ({
    M: "var(--git-modified)", A: "var(--git-added)", D: "var(--git-deleted)",
    R: "var(--git-renamed)", "?": "var(--git-untracked)", staged: "var(--git-staged)", clean: "transparent",
  } as Record<GitStatus, string>)[s] ?? "transparent";

  const ChangeRow = ({ c }: { c: GitChange }) => (
    <div onClick={() => onToggleStage(c.path)}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 14px", cursor: "pointer", borderBottom: "1px solid var(--border-subtle)", transition: "background 0.1s" }}
      onMouseEnter={e => (e.currentTarget.style.background = "var(--border-subtle)")}
      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
    >
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: c.staged ? "var(--git-staged)" : statusColor(c.status), flexShrink: 0 }} />
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: c.staged ? "var(--text)" : "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
        {c.path.split("/").pop()}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: c.staged ? "var(--git-staged)" : statusColor(c.status) }}>
        {c.staged ? "staged" : c.status}
      </span>
    </div>
  );

  return (
    <div style={{ height: "var(--gitpanel-h)", background: "var(--surface)", borderTop: "1px solid var(--border)", display: "flex", flexShrink: 0, overflow: "hidden" }}>
      {/* Unstaged */}
      <div style={{ flex: 1, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "7px 14px", display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Working Tree <span style={{ color: "var(--text-muted)", marginLeft: 4 }}>{unstaged.length}</span>
          </span>
          <button onClick={onStageAll}
            style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", background: "none", border: "none", cursor: "pointer", letterSpacing: "0.04em", transition: "color 0.12s" }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--text)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--text-faint)")}
          >Stage All ↑</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {unstaged.length === 0
            ? <div style={{ padding: "18px 14px", fontSize: 11, color: "var(--text-faint)", fontFamily: "var(--font-mono)" }}>clean</div>
            : unstaged.map(c => <ChangeRow key={c.path} c={c} />)
          }
        </div>
      </div>

      {/* Staged */}
      <div style={{ flex: 1, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "7px 14px", borderBottom: "1px solid var(--border-subtle)" }}>
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Staged <span style={{ color: "var(--git-staged)", marginLeft: 4 }}>{staged.length}</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {staged.length === 0
            ? <div style={{ padding: "18px 14px", fontSize: 11, color: "var(--text-faint)", fontFamily: "var(--font-mono)" }}>nothing staged</div>
            : staged.map(c => <ChangeRow key={c.path} c={c} />)
          }
        </div>
      </div>

      {/* Commit */}
      <div style={{ width: 260, display: "flex", flexDirection: "column", padding: 14, gap: 10, flexShrink: 0 }}>
        <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)", letterSpacing: "0.08em", textTransform: "uppercase" }}>Commit</div>
        <input ref={inputRef} value={commitMsg} onChange={e => setCommitMsg(e.target.value)}
          onKeyDown={e => e.key === "Enter" && onCommit()}
          placeholder="Commit message…"
          style={{
            background: "var(--bg)", border: `1px solid ${commitMsg ? "var(--accent)" : "var(--border)"}`,
            borderRadius: 7, padding: "8px 10px", fontSize: 12, fontFamily: "var(--font-mono)",
            color: "var(--text)", outline: "none", transition: "border-color 0.15s",
          }}
        />
        <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-faint)" }}>
          {staged.length} file{staged.length !== 1 ? "s" : ""} staged · main
        </div>
        <button onClick={onCommit} disabled={!commitMsg.trim() || staged.length === 0}
          style={{
            marginTop: "auto", padding: "9px 14px", borderRadius: 8, border: "none",
            background: commitDone ? "var(--git-staged)" : (commitMsg.trim() && staged.length > 0 ? "var(--accent)" : "var(--border)"),
            color: commitMsg.trim() && staged.length > 0 ? "var(--accent-fg)" : "var(--text-faint)",
            fontSize: 13, fontFamily: "var(--font-ui)", fontWeight: 600,
            cursor: commitMsg.trim() && staged.length > 0 ? "pointer" : "not-allowed",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
            transition: "background 0.2s, color 0.2s", letterSpacing: "-0.01em",
          }}
        >
          {commitDone ? <>{I.check} Committed</> : <>{I.commit} Commit to main</>}
        </button>
      </div>
    </div>
  );
}
