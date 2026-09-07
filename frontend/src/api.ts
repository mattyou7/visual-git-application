// ─── Visual Git API client ──────────────────────────────────────────────────
//
// Thin wrapper around fetch() for every endpoint exposed by app/api.py.
// Components should call these functions instead of using fetch() directly.
//
// In development, Vite proxies "/api" to the local FastAPI backend (see
// vite.config.ts), so these calls are same-origin relative URLs.

// ─── Shared types ───────────────────────────────────────────────────────────

export type GitStatus = "M" | "A" | "D" | "R" | "U" | "C" | "staged" | "clean";

export interface FileItem {
  id: string;
  name: string;
  type: "folder" | "file";
  ext?: string | null;
  size?: number | null;
  modified: string | null;
  gitStatus?: GitStatus | null;
  children?: FileItem[];
}

export interface RepositoryInfo {
  root: string;
  branch: string | null;
}

export interface GitChange {
  path: string;
  status: GitStatus;
  staged: boolean;
}

export interface WorkflowGuidance {
  happening: string;
  nextStep: string;
  actionLabel: string | null;
  action: string | null;
}

export interface GitStatusResponse {
  branch: string | null;
  changes: GitChange[];
  guidance: WorkflowGuidance;
  commit?: string;
}

export interface GitBranch {
  name: string;
  current: boolean;
}

export type MergeStatus = "current" | "upToDate" | "clean" | "conflict" | "unknown";

export interface GitBranchMergeability {
  name: string;
  current: boolean;
  mergeStatus: MergeStatus;
}

export interface GitCommit {
  hash: string;
  shortHash: string;
  author: string;
  timestamp: string;
  subject: string;
}

export interface GitRemote {
  name: string;
  fetchUrl: string;
  pushUrl: string;
}

export interface GitMergeResult {
  succeeded: boolean;
  message: string;
  conflicts: string[];
}

export interface GitConflictState {
  inProgress: boolean;
  conflicts: string[];
}

export interface RecentRepository {
  path: string;
  name: string;
  lastOpened: string;
  branch: string | null;
  dirty: boolean;
  available: boolean;
}

export interface SidebarLocation {
  id: string;
  label: string;
  path: string;
}

export interface GitHubStatus {
  connected: boolean;
  username: string | null;
}

export interface ApiErrorPayload {
  code: string;
  message: string;
}

export class ApiRequestError extends Error {
  code: string;
  status: number;

  constructor(status: number, error: ApiErrorPayload) {
    super(error.message);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.status = status;
  }
}

// ─── Core request helper ────────────────────────────────────────────────────

interface ApiEnvelope<T> {
  success: boolean;
  data?: T;
  error?: ApiErrorPayload;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  let body: ApiEnvelope<T> | null = null;
  try {
    body = (await response.json()) as ApiEnvelope<T>;
  } catch {
    // no JSON body (e.g. network failure) — fall through to generic error below
  }

  if (!response.ok || !body || body.success === false) {
    const error = body?.error ?? { code: "NETWORK_ERROR", message: `Request to ${path} failed (${response.status}).` };
    throw new ApiRequestError(response.status, error);
  }

  return body.data as T;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
}

function del<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "DELETE", body: body !== undefined ? JSON.stringify(body) : undefined });
}

// ─── Health ──────────────────────────────────────────────────────────────────

export function getHealth(): Promise<{ ok: boolean }> {
  return fetch("/api/health").then((r) => r.json());
}

// ─── Filesystem ──────────────────────────────────────────────────────────────

export interface ListFilesResult {
  path: string;
  repository: RepositoryInfo | null;
  items: FileItem[];
}

export function getFiles(path: string): Promise<ListFilesResult> {
  return get<ListFilesResult>(`/api/files?path=${encodeURIComponent(path)}`);
}

export function getFilesTree(path: string, depth = 1): Promise<FileItem> {
  return get<FileItem>(`/api/files/tree?path=${encodeURIComponent(path)}&depth=${depth}`);
}

export function createFolder(parent: string, name: string): Promise<{ path: string }> {
  return post("/api/files/folder", { parent, name });
}

export function renameFile(path: string, newName: string): Promise<{ path: string }> {
  return post("/api/files/rename", { path, newName });
}

export function deleteFiles(paths: string[]): Promise<{ deleted: string[] }> {
  return del("/api/files", { paths });
}

export function copyFiles(sources: string[], destination: string): Promise<{ paths: string[] }> {
  return post("/api/files/copy", { sources, destination });
}

export function moveFiles(sources: string[], destination: string): Promise<{ paths: string[] }> {
  return post("/api/files/move", { sources, destination });
}

export function openFile(path: string): Promise<Record<string, never>> {
  return post("/api/files/open", { path });
}

export function getLocations(): Promise<SidebarLocation[]> {
  return get<SidebarLocation[]>("/api/files/locations");
}

export interface SearchMatch {
  path: string;
  type: "file" | "folder";
  name: string;
}

export function searchFiles(query: string, path?: string): Promise<SearchMatch[]> {
  const params = new URLSearchParams({ query });
  if (path) params.set("path", path);
  return get<SearchMatch[]>(`/api/files/search?${params.toString()}`);
}

// ─── Clipboard (copy/cut/paste) ─────────────────────────────────────────────

export function clipboardCopy(paths: string[]): Promise<Record<string, never>> {
  return post("/api/clipboard/copy", { paths });
}

export function clipboardCut(paths: string[]): Promise<Record<string, never>> {
  return post("/api/clipboard/cut", { paths });
}

export function clipboardPaste(destination: string): Promise<{ paths: string[] }> {
  return post("/api/clipboard/paste", { destination });
}

export function getClipboard(): Promise<{ mode: "copy" | "cut" | null; paths: string[] }> {
  return get("/api/clipboard");
}

// ─── Repository ──────────────────────────────────────────────────────────────

export interface OpenRepositoryResult {
  path: string;
  repository: RepositoryInfo | null;
}

export function openRepository(path: string): Promise<OpenRepositoryResult> {
  return post<OpenRepositoryResult>("/api/repository/open", { path });
}

export function getCurrentRepository(): Promise<OpenRepositoryResult> {
  return get<OpenRepositoryResult>("/api/repository/current");
}

export function getRecentRepositories(): Promise<RecentRepository[]> {
  return get<RecentRepository[]>("/api/repositories/recent");
}

// ─── Git status / staging / commit ──────────────────────────────────────────

export function getGitStatus(): Promise<GitStatusResponse> {
  return get<GitStatusResponse>("/api/git/status");
}

export function stageFiles(paths: string[]): Promise<GitStatusResponse> {
  return post("/api/git/stage", { paths });
}

export function stageAll(): Promise<GitStatusResponse> {
  return post("/api/git/stage-all");
}

export function unstageFiles(paths: string[]): Promise<GitStatusResponse> {
  return post("/api/git/unstage", { paths });
}

export function commit(message: string): Promise<GitStatusResponse> {
  return post("/api/git/commit", { message });
}

// ─── Branches ────────────────────────────────────────────────────────────────

export function getBranches(): Promise<GitBranch[]> {
  return get<GitBranch[]>("/api/git/branches");
}

export function getBranchMergeability(): Promise<GitBranchMergeability[]> {
  return get<GitBranchMergeability[]>("/api/git/branches/mergeability");
}

export function createBranch(name: string, startPoint?: string | null): Promise<{ branches: GitBranch[]; repository: RepositoryInfo }> {
  return post("/api/git/branches", { name, startPoint: startPoint ?? null });
}

export function switchBranch(name: string): Promise<{ repository: RepositoryInfo; status: GitStatusResponse }> {
  return post("/api/git/branches/switch", { name });
}

export function renameBranch(oldName: string, newName: string): Promise<GitBranch[]> {
  return post("/api/git/branches/rename", { oldName, newName });
}

export function deleteBranch(name: string): Promise<GitBranch[]> {
  return del("/api/git/branches", { name });
}

// ─── History / graph ─────────────────────────────────────────────────────────

export function getHistory(limit = 100): Promise<GitCommit[]> {
  return get<GitCommit[]>(`/api/git/history?limit=${limit}`);
}

export function getGraph(limit = 200): Promise<GitCommit[]> {
  return get<GitCommit[]>(`/api/git/graph?limit=${limit}`);
}

export function getFileHistory(path: string, limit = 100): Promise<GitCommit[]> {
  return get<GitCommit[]>(`/api/git/file-history?path=${encodeURIComponent(path)}&limit=${limit}`);
}

export function getFileAtCommit(commitHash: string, path: string): Promise<{ path: string; commit: string; content: string }> {
  return get(`/api/git/file-at-commit?commit=${encodeURIComponent(commitHash)}&path=${encodeURIComponent(path)}`);
}

export function copyFileFromCommit(commitHash: string, path: string): Promise<GitStatusResponse> {
  return post("/api/git/copy-file-from-commit", { commit: commitHash, path });
}

export function getDiff(path: string, older?: string, newer?: string): Promise<{ diff: string }> {
  const params = new URLSearchParams({ path });
  if (older) params.set("older", older);
  if (newer) params.set("newer", newer);
  return get(`/api/git/diff?${params.toString()}`);
}

// ─── Merge / conflicts ───────────────────────────────────────────────────────

export function mergeBranch(branch: string): Promise<GitMergeResult> {
  return post("/api/git/merge", { branch });
}

export function getConflicts(): Promise<GitConflictState> {
  return get<GitConflictState>("/api/git/conflicts");
}

export function useCurrent(path: string): Promise<GitStatusResponse> {
  return post("/api/git/conflicts/use-current", { path });
}

export function useIncoming(path: string): Promise<GitStatusResponse> {
  return post("/api/git/conflicts/use-incoming", { path });
}

export function markResolved(path: string): Promise<GitStatusResponse> {
  return post("/api/git/conflicts/mark-resolved", { path });
}

// ─── Remotes ─────────────────────────────────────────────────────────────────

export function getRemotes(): Promise<GitRemote[]> {
  return get<GitRemote[]>("/api/git/remotes");
}

export function addRemote(name: string, url: string): Promise<GitRemote[]> {
  return post("/api/git/remotes", { name, url });
}

export function removeRemote(name: string): Promise<GitRemote[]> {
  return del(`/api/git/remotes/${encodeURIComponent(name)}`);
}

// ─── Not-yet-implemented Git features ───────────────────────────────────────
// These intentionally reject with a clear "not implemented" ApiRequestError
// (HTTP 501) rather than a network error, so the UI can show a real message.

export function clone(): Promise<never> {
  return post("/api/git/clone");
}

export function push(): Promise<never> {
  return post("/api/git/push");
}

export function pull(): Promise<never> {
  return post("/api/git/pull");
}

// ─── GitHub ──────────────────────────────────────────────────────────────────

export function getGitHubStatus(): Promise<GitHubStatus> {
  return get<GitHubStatus>("/api/github/status");
}

export function connectGitHub(): Promise<GitHubStatus> {
  return post("/api/github/connect");
}

export function disconnectGitHub(): Promise<GitHubStatus> {
  return post("/api/github/disconnect");
}

export function createGitHubRepo(name: string, isPrivate = true): Promise<{ name: string; fullName: string; cloneUrl: string; private: boolean }> {
  return post("/api/github/repos", { name, private: isPrivate });
}

// ─── Formatting helpers ──────────────────────────────────────────────────────

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unitIndex]}`;
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.floor(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.floor(hours)} hr ago`;
  const days = hours / 24;
  if (days < 7) return `${Math.floor(days)} days ago`;
  const weeks = days / 7;
  if (weeks < 5) return `${Math.floor(weeks)} wks ago`;
  const months = days / 30;
  if (months < 12) return `${Math.floor(months)} mos ago`;
  return `${Math.floor(months / 12)} yrs ago`;
}