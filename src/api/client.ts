import type {
  ConceptSearchRequest, ConceptSearchResult, SearchProgress, SearchRequest, SearchResult,
} from "../types";
import { readEventStream } from "./stream";

const BASE = ((import.meta.env.VITE_API_URL as string | undefined) || "/api").replace(/\/$/, "");
/** Sample mode must be explicitly enabled; unavailable services never produce fake evidence. */
export const usingMockApi = import.meta.env.VITE_USE_MOCK === "true";

async function responseError(res: Response, action: string): Promise<Error> {
  const data = await res.json().catch(() => null) as { detail?: unknown; message?: unknown } | null;
  const detail = data?.detail ?? data?.message;
  const message = typeof detail === "string" && detail.length < 600 ? detail : null;
  return new Error(message || `${action} failed (${res.status}). Check that the API and search index are available.`);
}

export async function searchIdea(
  req: SearchRequest,
  onProgress?: (p: SearchProgress) => void,
  signal?: AbortSignal,
): Promise<SearchResult> {
  if (usingMockApi) return (await import("./mock")).mockSearch(req, onProgress, signal);
  onProgress?.({ stage: "keywords", message: "Parsing your question and study plan" });
  const options: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(req), signal,
  };
  let res: Response;
  try {
    res = await fetch(`${BASE}/search/stream`, options);
    // Compatibility with servers exposing only the ordinary JSON endpoint.
    if (res.status === 404 || res.status === 405) {
      res = await fetch(`${BASE}/search`, { ...options, headers: { "Content-Type": "application/json" } });
    }
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof TypeError) throw new Error("Cannot reach the search service. Start the API or check your connection, then retry.");
    throw error;
  }
  if (!res.ok) throw await responseError(res, "Search");
  if (!res.headers.get("content-type")?.includes("text/event-stream")) {
    return await res.json() as SearchResult;
  }
  if (!res.body) throw new Error("The search service returned an empty response.");
  let result: SearchResult | undefined;
  await readEventStream(res.body, (event, data) => {
    if (!data || typeof data !== "object") throw new Error("The search service returned an invalid event.");
    if (event === "progress") onProgress?.(data as SearchProgress);
    if (event === "result") result = data as SearchResult;
    if (event === "error") {
      const message = (data as { message?: unknown }).message;
      throw new Error(typeof message === "string" ? message : "Search could not complete. Please retry.");
    }
  }, signal);
  if (!result) throw new Error("Search ended before results arrived. Please retry.");
  return result;
}

export async function searchConcepts(
  req: ConceptSearchRequest,
  signal?: AbortSignal,
): Promise<ConceptSearchResult> {
  if (usingMockApi) return (await import("./conceptsMock")).mockConceptSearch(req, signal);
  let res: Response;
  try {
    res = await fetch(`${BASE}/concepts/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal,
    });
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof TypeError) throw new Error("Cannot reach the search service. Start the API or check your connection, then retry.");
    throw error;
  }
  if (!res.ok) throw await responseError(res, "Concept search");
  return await res.json() as ConceptSearchResult;
}
