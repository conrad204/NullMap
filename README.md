# nullMap

Search the literature for prior attempts at a research idea, sorted by what they found.
Surfaces credible null results and unreported studies that a normal search buries, and
estimates whether the idea is still worth pursuing.

## Frontend

```sh
npm install
npm run dev        # http://localhost:5173, runs on sample data
npm run build      # typecheck + production build
```

With no `VITE_API_URL` set, the UI runs against the mock in `src/api/mock.ts` and shows a
"sample data" badge. Copy `.env.example` to `.env` and set the URL once a backend exists.

## API contract (for the backend)

Types live in [`src/types.ts`](src/types.ts). Endpoints the client calls, in
[`src/api/client.ts`](src/api/client.ts):

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/search` | `SearchRequest` (JSON) | `SearchResult` |
| GET | `/search/:queryId/events` | | `SearchProgress` stream (SSE), optional |
| POST | `/contributions` | multipart: `title`, `description`, `outcome`, `ownershipAcknowledged`, `files[]` | `ContributionReceipt` |

Every paper in a `SearchResult` carries one of five verdicts:
`effect`, `credible_null`, `inconclusive`, `failed`, `unreported`.
The `estimate` block holds the probability of a meaningful effect, an EV score on a -1..1
scale, a recommendation, and plain-language drivers.
