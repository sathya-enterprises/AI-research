# Benchmark scorecard: today-fixes-9

11 prompts | replayed with the current code on 2026-11-16 01:04 | compared with `before-today`

| Prompt | Live outcome | Charts published | Words (written/plan) | Numbers removed | Unsupported claims | Chart gate failures | Chart candidates verified |
|---|---|---:|---|---:|---:|---:|---|
| ev_india_detailed | published | 2 | 6030/7614 | 0 (-25) | 0 | 0 | 14 (+7)/22 |
| ev_india_in_depth | draft | 5 | 12896/14040 | 11 (-6) | 0 (-1) | 0 (-35) | 23 (-11)/45 |
| banking_asset_quality | published | 0 | 4602/5060 | 1 | 0 | 0 | 3/3 |
| markets_sector_rotation | published | 4 | 6710/5980 | 4 (-1) | 0 | 0 | 15 (+3)/17 |
| renewables_capacity | published | 0 | 4624/4872 | 1 | 0 | 0 | 7 (+3)/7 |
| healthcare_sea | published | 2 | 5888/7503 | 27 (-7) | 0 | 0 (-31) | 7 (+3)/14 |
| semiconductors_global | published | 0 | 5263/5490 | 9 | 0 | 0 | 4 (+2)/4 |
| internet_use_comparison | published | 2 | 2544/5560 | 6 | 0 | 0 (-38) | 6/13 |
| pms_workbook | published | 7 | 5727/7606 | 5 | 1 | 0 | 0/3 |
| insurance_penetration | published | 0 | 4192/5610 | 3 | 0 | 0 | 0/2 |
| telecom_5g | published | 0 | 4735/5290 | 29 | 0 | 0 | 0/2 |

## Totals (replay, current code)

- numbers removed: **96**
- unsupported claims after the fact-sheet check: **1**
- chart gate failures on published charts: **0**
- chart candidates verified: **79 / 132**

## Change against the previous scorecard

- numbers removed: 103 -> 96 (better)
- unsupported claims: 2 -> 1 (better)
- chart gate failures: 104 -> 0 (better)
- chart candidates verified: 72 -> 79 (better)
- prompts worse on any metric: ev_india_in_depth

## Why charts were rejected / numbers removed (replay)

- **ev_india_detailed**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: –
- **ev_india_in_depth**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 41758, 1000414 not in the cited passages; is in the evidence for another metric or entity; is not a well-formed number; is not in the evidence
- **banking_asset_quality**: charts: – | numbers: is in the evidence for another metric or entity
- **markets_sector_rotation**: charts: The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 67.54 not in the cited passages; is in the evidence for another metric or entity
- **renewables_capacity**: charts: – | numbers: calculation uses numbers that are not facts in the evidence
- **healthcare_sea**: charts: The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence
- **semiconductors_global**: charts: – | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **internet_use_comparison**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated unit.; The quoted source row does not support the chart point's entity or category. | numbers: is in the evidence for another metric or entity; is not in the evidence
- **pms_workbook**: charts: The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not support the chart point's entity or category. | numbers: calculation uses numbers that are not facts in the evidence; is not in the evidence
- **insurance_penetration**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages
- **telecom_5g**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 10.38, 9.12 not in the cited passages; input(s) 17.01 not in the cited passages; input(s) 211.36, 176.59 not in the cited passages; input(s) 231.46, 176.59 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence; row: 12,732,415 not in the evidence; row: 19,430 not in the evidence; row: 941.82 not in the evidence
