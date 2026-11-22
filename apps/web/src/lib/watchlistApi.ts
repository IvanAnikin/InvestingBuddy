/**
 * V3.21.2 — the team Shortlist / Watchlist and the monitoring signal feed (ADR-109).
 *
 * Browser-only: every call goes through the same-origin admin proxy, which forwards the
 * session's X-IB-Admin-Email; the backend records a hash of it on each entry. A monitoring
 * signal is an observation, never a conclusion, and nothing here starts research.
 *
 * Keys pinned to apps/api/app/schemas/watchlists.py.
 */

const WATCHLISTS = "/api/admin/proxy/api/v1/watchlists";
const MONITORING = "/api/admin/proxy/api/v1/monitoring";

export type TeamListName = "Shortlist" | "Watchlist";

export type EntrySource = "manual" | "discovery_candidate" | "report" | "auto_report";

export type WatchlistSummary = {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  is_team_list: boolean;
  created_at: string | null;
  entry_count: number;
  open_signal_count: number;
};

export type MonitoringSignal = {
  id: string;
  company_id: string | null;
  kind: string;
  summary: string;
  status: "open" | "acknowledged" | "superseded";
  relates_to_question_key: string | null;
  relates_to_run_id: string | null;
  source_ref: string | null;
  detail: Record<string, unknown>;
  observed_at: string;
  acknowledged_at: string | null;
  material: boolean;
};

export type LatestRecord = {
  id: string;
  research_run_id: string | null;
  as_of: string;
  verdict: string | null;
  verdict_meaning: string | null;
  composite_score: number | null;
};

export type WatchlistEntry = {
  id: string;
  company_id: string | null;
  company: { id: string; ticker: string | null; exchange: string | null; name: string | null } | null;
  note: string | null;
  source: EntrySource | null;
  added_at: string;
  added_by_hash: string | null;
  latest_record: LatestRecord | null;
  open_signals: MonitoringSignal[];
};

export type WatchlistDetail = WatchlistSummary & { entries: WatchlistEntry[] };

export type EntryInput = {
  company_id?: string | null;
  ticker?: string | null;
  exchange?: string | null;
  note?: string | null;
  source: EntrySource;
};

export type EntryCreated = {
  entry_id: string;
  watchlist_id: string;
  company_id: string;
  created: boolean;
};

export type MonitoringRunResult = {
  mode: "enqueued" | "inline";
  job_id: string | null;
  created: boolean | null;
  result: { companies: number; persisted: number; reason: string | null } | null;
};

async function send<T>(url: string, init?: RequestInit): Promise<T | null> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : Array.isArray(d) ? "Please check the entered values." : detail;
      }
    } catch {
      // keep the status fallback
    }
    throw new Error(detail);
  }
  return res.status === 204 ? null : ((await res.json()) as T);
}

export const listWatchlists = async (): Promise<WatchlistSummary[]> =>
  (await send<WatchlistSummary[]>(WATCHLISTS)) ?? [];

export const getWatchlist = async (id: string): Promise<WatchlistDetail | null> =>
  send<WatchlistDetail>(`${WATCHLISTS}/${encodeURIComponent(id)}`);

export const addEntry = async (id: string, input: EntryInput): Promise<EntryCreated | null> =>
  send<EntryCreated>(`${WATCHLISTS}/${encodeURIComponent(id)}/entries`, {
    method: "POST",
    body: JSON.stringify(input),
  });

export const removeEntry = async (id: string, entryId: string): Promise<void> => {
  await send(`${WATCHLISTS}/${encodeURIComponent(id)}/entries/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
};

/** Add to a team list by name; the lists are created by the backend on first read. */
export async function addToTeamList(name: TeamListName, input: EntryInput): Promise<EntryCreated | null> {
  const lists = await listWatchlists();
  const target = lists.find((w) => w.is_team_list && w.name === name);
  if (!target) throw new Error(`The ${name} list is not available.`);
  return addEntry(target.id, input);
}

export const listSignals = async (
  params: { status?: "open" | "acknowledged" | "superseded" | "all"; companyId?: string } = {},
): Promise<MonitoringSignal[]> => {
  const q = new URLSearchParams();
  q.set("status", params.status ?? "open");
  if (params.companyId) q.set("company_id", params.companyId);
  return (await send<MonitoringSignal[]>(`${MONITORING}/signals?${q.toString()}`)) ?? [];
};

export const acknowledgeSignal = async (id: string): Promise<MonitoringSignal | null> =>
  send<MonitoringSignal>(`${MONITORING}/signals/${encodeURIComponent(id)}/acknowledge`, {
    method: "POST",
  });

export const runMonitoring = async (
  scope: { watchlist_id: string } | { company_id: string },
): Promise<MonitoringRunResult | null> =>
  send<MonitoringRunResult>(`${MONITORING}/run`, { method: "POST", body: JSON.stringify(scope) });

/** Plain labels for the closed signal vocabulary (apps/api detector.SIGNAL_KINDS). */
export const SIGNAL_KIND_LABELS: Record<string, string> = {
  new_document: "New document",
  new_ir_event: "New IR event",
  transcript_published: "Transcript published",
  research_delta: "Research change",
  open_gap_closable: "Open gap now closable",
  macro_revision: "Macro revision",
  earnings_released: "Results filed",
  consensus_revision: "Expectation revised",
  passive_ownership_change: "Fund ownership moved",
  relationship_change: "Relationship change",
  competitor_capacity_announcement: "Peer capacity change",
  change_event: "Change event",
  missing_information: "Missing information",
  catalyst_window: "Dated item within 30 days",
};
