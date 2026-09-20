---
name: nullmap-mock-ui
description: Run browser-level nullMap frontend checks without Elasticsearch or backend credentials.
---

# Mock frontend testing

From the repository root, use existing dependencies or the blueprint's npm install step, then start:

`VITE_USE_MOCK=true npm run dev -- --host 0.0.0.0`

Open the Vite URL. No backend or Elasticsearch is required. The home page's concept composer is below the main question composer. `/?state=results` opens the deterministic evidence fixture; inspect `src/api/mock.ts` for current expected counts rather than relying on historical values.

The desktop study list scrolls independently in its right sidebar. Legend buttons and the unstated/inconclusive notes filter the list; the colored summary bar itself is a noninteractive image. Match filtered titles and counts, not just selected color.

For concept inputs, test both committed chips and text still being typed when Search is clicked. Input blur can change the layout, so click once and observe before retrying. Compare document scrollWidth and clientWidth when testing long unbroken terms. After editing a searched combination, verify retained results identify their original query.

Mock data is fictional. It can validate rendering and explicit mock errors, not embedding quality, actual API status codes, Elasticsearch aggregation, or partial-vector cancellation.

## Devin Secrets Needed

None for mock frontend testing. Never put service credentials in VITE_* variables.
