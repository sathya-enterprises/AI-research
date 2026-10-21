# Action Reliability v29 — Visual Alternatives and Clear Completion

## What changed

- Prompts phrased as **"table or chart"** are treated as genuine alternatives. The Action engine no longer launches a mandatory chart-only generation step and then rejects the whole edit because chart JSON could not be produced.
- Existing or meaningfully updated evidence tables can satisfy a table-or-chart request. If the edited section contains no usable table, the engine first tries the normal focused table pass and then has a deterministic, evidence-only table fallback based on sentences and citations already present in the section/research context.
- The deterministic fallback adds no numbers or factual claims; its cells are copied from cited/numerical evidence already present in the candidate/context and then validated against the evidence manifest.
- Table validation now allows an explicitly requested additional visual instead of incorrectly rejecting it solely because the edited section contains one more table than the original. Non-requested duplicate tables are still checked.
- A chart-only request remains fail-closed when the chart cannot be safely created. Existing chart synchronization remains protected when numeric evidence changes.
- Action result payloads now expose `tableAdded`, `visualSatisfied`, and optional `visualWarning`, including in multi-section results, so clients can distinguish success from successful visual fallback.
- The Growth Gradual icon styles now set width and height together in both the active top-level frontend and the bundled backend frontend, avoiding Next.js image aspect-ratio warnings.

## Validation

Focused Action suites cover visual-alternative parsing, multi-section editing, preservation of unrelated sections, table fallback using existing citations/numbers, fresh citation handling, and explicit chart-only failure behavior.
