# Benchmark scorecard: calc-labels

21 prompts | replayed with the current code on 2026-11-16 02:30 | compared with `after-fixes`

| Prompt | Live outcome | Charts published | Words (written/plan) | Numbers removed | Unsupported claims | Chart gate failures | Chart candidates verified |
|---|---|---:|---|---:|---:|---:|---|
| ev_india_detailed | published | 2 | 6030/7614 | 0 | 0 | 0 | 14/22 |
| ev_india_in_depth | draft | 5 | 12896/14040 | 11 | 0 | 0 | 23/45 |
| banking_asset_quality | published | 0 | 4602/5060 | 1 | 0 | 0 | 3/3 |
| markets_sector_rotation | published | 4 | 6710/5980 | 4 | 0 | 0 | 15/17 |
| renewables_capacity | published | 0 | 4624/4872 | 1 | 0 | 0 | 7/7 |
| healthcare_sea | published | 2 | 5888/7503 | 27 | 0 | 0 | 7/14 |
| semiconductors_global | published | 0 | 5263/5490 | 9 | 0 | 0 | 4/4 |
| internet_use_comparison | published | 2 | 2544/5560 | 6 | 0 | 0 | 6/13 |
| pms_workbook | published | 7 | 5727/7606 | 5 | 1 | 0 | 1/3 |
| insurance_penetration | published | 0 | 4192/5610 | 3 | 0 | 0 | 0/2 |
| telecom_5g | published | 0 | 4735/5290 | 29 | 0 | 0 | 0/2 |
| pharma_exports | published | 1 | 5833/6522 | 4 | 0 | 0 | 1/2 |
| fmcg_rural | published | 2 | 5511/5356 | 11 | 0 | 0 | 3/4 |
| real_estate_housing | published | 5 | 6252/7533 | 9 | 0 | 0 | 7/9 |
| agri_msp | published | 0 | 6226/7586 | 29 (-4) | 0 | 0 | 0/5 |
| india_inflation | published | 0 | 4698/6582 | 6 | 0 | 0 | 0/14 |
| us_labour_market | published | 2 | 4367/4642 | 13 | 0 | 0 | 4/5 |
| europe_gas | published | 0 | 5343/4800 | 0 | 0 | 0 | 0/0 |
| china_exports | published | 0 | 7291/7615 | 26 | 0 | 0 | 0/3 |
| ai_investment | published | 3 | 6903/7679 | 18 | 0 | 0 | 3/11 |
| india_ipo_market | published | 2 | 6613/7511 | 6 | 0 | 0 | 6/11 |

## Totals (replay, current code)

- numbers removed: **218**
- unsupported claims after the fact-sheet check: **1**
- chart gate failures on published charts: **0**
- chart candidates verified: **104 / 196**

## Change against the previous scorecard (19 prompts in both)

- numbers removed: 198 -> 194 (better)
- unsupported claims: 1 -> 1 (same)
- chart gate failures: 0 -> 0 (same)
- chart candidates verified: 95 -> 95 (same)
- prompts new in this scorecard (not compared): ai_investment, india_ipo_market
- prompts worse on any metric: none

## Why charts were rejected / numbers removed (replay)

- **ev_india_detailed**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: –
- **ev_india_in_depth**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 41758, 1000414 not in the cited passages; is in the evidence for another metric or entity; is not a well-formed number; is not in the evidence
- **banking_asset_quality**: charts: – | numbers: is in the evidence for another metric or entity
- **markets_sector_rotation**: charts: The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 67.54 not in the cited passages; is in the evidence for another metric or entity
- **renewables_capacity**: charts: – | numbers: calculation uses numbers that are not facts in the evidence
- **healthcare_sea**: charts: The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence
- **semiconductors_global**: charts: – | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **internet_use_comparison**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated unit.; The quoted source row does not support the chart point's entity or category. | numbers: is in the evidence for another metric or entity; is not in the evidence
- **pms_workbook**: charts: The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated measure. | numbers: calculation uses numbers that are not facts in the evidence; is not in the evidence
- **insurance_penetration**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages
- **telecom_5g**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 10.38, 9.12 not in the cited passages; input(s) 17.01 not in the cited passages; input(s) 211.36, 176.59 not in the cited passages; input(s) 231.46, 176.59 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence; row: 12,732,415 not in the evidence; row: 19,430 not in the evidence; row: 941.82 not in the evidence
- **pharma_exports**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not a well-formed number
- **fmcg_rural**: charts: The chart value does not match the quoted source row, period, or named table column. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 1 not in the cited passages; is in the evidence for another metric or entity
- **real_estate_housing**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; input(s) 335476 not in the cited passages; input(s) 82,892, 70,229 not in the cited passages; is in the evidence for another metric or entity; row: 35.5 not in the evidence; row: 36.6 not in the evidence
- **agri_msp**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **india_inflation**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages; is in the evidence for another metric or entity
- **us_labour_market**: charts: The chart value does not match the quoted source row, period, or named table column. | numbers: is in the evidence for another metric or entity; is not in the evidence; row: 17.7 not in the evidence; row: 50.5 not in the evidence; row: 72.3 not in the evidence; row: 76.8 not in the evidence
- **china_exports**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **ai_investment**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 1 not in the cited passages; is in the evidence for another metric or entity
- **india_ipo_market**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not a well-formed number
