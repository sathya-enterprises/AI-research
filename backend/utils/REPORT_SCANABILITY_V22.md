# Report scanability pass v22

The report writer already had guidance to prefer tables, bullets, timelines, risk matrices and mixed-format sections when supported by the evidence. Deep Research output showed that these instructions were not reliably followed by every initial or continuation generation.

V22 adds a post-generation, section-local formatting pass. It selects prose-heavy H2/H3/H4 blocks by measuring long-paragraph runs and existing structured elements. A small number of selected blocks are restructured into evidence-appropriate Markdown bullets, comparison tables or concise callouts. The pass is formatting-only: it must retain all citation IDs, URLs, numeric anchors and chart/image placeholders, cannot add numeric claims, and must demonstrate scanability improvement before its rewrite is accepted. If a provider fails, returns a prose-only rewrite, drops evidence, or does not improve structure, the original block is retained.

V22 also removes leaked continuation-control marker lines such as `[[SECTION]]` and `[[END SECTION]]` before pagination and final rendering. This prevents generation protocol artifacts from appearing as reader-facing headings.

The changes are dynamic and tier-aware: the pass selects fewer candidates for Brief, more for In-Detail, and a capped set for Deep Research. It does not force charts or tables when source data does not support them and does not impose a fixed report template.
