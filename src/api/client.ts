import type {
  ContributionReceipt,
  ContributionRequest,
  SearchProgress,
  SearchRequest,
  SearchResult,
} from "../types";
import { mockSearch, mockSubmitContribution } from "./mock";

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "");

/** True when no backend is configured and the UI is running on sample data. */
export const usingMockApi = !BASE;

/**
 * POST /search
 * Body: SearchRequest. Response: SearchResult.
 *
 * Progress events: the backend may stream SearchProgress as server-sent events
 * from GET /search/:queryId/events. Until that exists, the real client only
 * reports the final result and the skeleton shows the first stage.
 */
export async function searchIdea(
  req: SearchRequest,
  onProgress?: (p: SearchProgress) => void,
  signal?: AbortSignal,
): Promise<SearchResult> {
  if (!BASE) return mockSearch(req, onProgress, signal);

  onProgress?.({ stage: "keywords", message: "Starting search" });
  const res = await fetch(`${BASE}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok) throw new Error(`Search failed (${res.status} ${res.statusText})`);
  return (await res.json()) as SearchResult;
}

/**
 * POST /contributions  (multipart/form-data)
 * Fields: title, description, outcome, ownershipAcknowledged, files[]
 * Response: ContributionReceipt.
 */
export async function submitContribution(
  req: ContributionRequest,
  signal?: AbortSignal,
): Promise<ContributionReceipt> {
  if (!BASE) return mockSubmitContribution(req, signal);

  const form = new FormData();
  form.set("title", req.title);
  form.set("description", req.description);
  form.set("outcome", req.outcome);
  form.set("ownershipAcknowledged", String(req.ownershipAcknowledged));
  for (const f of req.files) form.append("files", f, f.name);

  const res = await fetch(`${BASE}/contributions`, { method: "POST", body: form, signal });
  if (!res.ok) throw new Error(`Upload failed (${res.status} ${res.statusText})`);
  return (await res.json()) as ContributionReceipt;
}
