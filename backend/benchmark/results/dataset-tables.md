# Benchmark scorecard: dataset-tables

30 prompts | replayed with the current code on 2026-11-17 11:41 | compared with `source-identity`

| Prompt | Live outcome | Charts published | Words (written/plan) | Numbers removed | Unsupported claims | Chart gate failures | Chart candidates verified |
|---|---|---:|---|---:|---:|---:|---|
| ev_india_detailed | published | 2 | 6030/7614 | 0 | 0 | 0 | 16/22 |
| ev_india_in_depth | draft | 5 | 12896/14040 | 10 | 0 | 0 | 30/44 |
| banking_asset_quality | published | 0 | 4602/5060 | 1 | 0 | 0 | 3/3 |
| markets_sector_rotation | published | 4 | 6710/5980 | 2 | 0 | 0 | 15/17 |
| renewables_capacity | published | 0 | 4624/4872 | 0 | 0 | 0 | 7/7 |
| healthcare_sea | published | 2 | 5888/7503 | 18 | 0 | 0 | 9 (+2)/14 |
| semiconductors_global | published | 0 | 5263/5490 | 7 | 0 | 0 | 4/4 |
| internet_use_comparison | published | 2 | 2544/5560 | 6 | 0 | 0 | 7 (+1)/13 |
| pms_workbook | published | 7 | 5727/7606 | 3 | 1 | 0 | 1/3 |
| insurance_penetration | published | 0 | 4192/5610 | 1 | 0 | 0 | 0/2 |
| telecom_5g | published | 0 | 4735/5290 | 15 | 0 | 0 | 1/2 |
| pharma_exports | published | 1 | 5833/6522 | 4 | 0 | 0 | 1/2 |
| fmcg_rural | published | 2 | 5511/5356 | 8 | 0 | 0 | 3/4 |
| real_estate_housing | published | 5 | 6252/7533 | 9 | 0 | 0 | 8/9 |
| agri_msp | published | 0 | 6226/7586 | 25 | 2 | 0 | 1/5 |
| india_inflation | published | 0 | 4698/6582 | 5 | 0 | 0 | 0/12 |
| us_labour_market | published | 2 | 4367/4642 | 14 | 0 | 0 | 4/5 |
| europe_gas | published | 0 | 5343/4800 | 0 | 0 | 0 | 0/0 |
| china_exports | published | 0 | 7291/7615 | 15 | 0 | 0 | 2/3 |
| ai_investment | published | 3 | 6903/7679 | 8 | 0 | 0 | 6/11 |
| india_ipo_market | published | 2 | 6613/7511 | 2 | 0 | 0 | 8/10 |
| mutual_fund_sip | published | 1 | 4980/5019 | 13 (+7) | 0 | 0 | 4/6 |
| steel_production | published | 1 | 3721/4920 | 7 | 1 | 0 | 7/10 |
| oil_prices_india | published | 1 | 5666/5832 | 10 | 0 | 0 | 8/9 |
| defence_exports | published | 1 | 5121/6140 | 5 | 0 | 0 | 3/3 |
| tourism_recovery | published | 2 | 6025/7704 | 17 (+3) | 0 | 0 | 6 (+1)/16 |
| education_outcomes | published | 0 | 6294/7512 | 9 | 0 | 0 | 0/2 |
| upi_payments | published | 0 | 6066/7500 | 8 | 0 | 0 | 1/5 |
| auto_sales_ice | published | 0 | 4785/5840 | 3 | 0 | 0 | 1/5 |
| climate_emissions | published | 1 | 6948/7739 | 4 | 0 | 0 | 2/2 |

## Totals (replay, current code)

- numbers removed: **229**
- unsupported claims after the fact-sheet check: **4**
- chart gate failures on published charts: **0**
- chart candidates verified: **158 / 250**

## Change against the previous scorecard (30 prompts in both)

- numbers removed: 219 -> 229 (WORSE)
- unsupported claims: 4 -> 4 (same)
- chart gate failures: 0 -> 0 (same)
- chart candidates verified: 154 -> 158 (better)
- prompts worse on any metric: mutual_fund_sip, tourism_recovery

## Why charts were rejected / numbers removed (replay)

- **ev_india_detailed**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source row does not support the chart point's exact period. | numbers: –
- **ev_india_in_depth**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source row does not support the chart point's exact period. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 41758, 1000414 not in the cited passages; is in the evidence for another metric or entity; is not a well-formed number; is not in the evidence
- **banking_asset_quality**: charts: – | numbers: is in the evidence for another metric or entity
- **markets_sector_rotation**: charts: The quoted source value cannot be tied to a local row or statement. | numbers: has a different unit in the evidence; input(s) 67.54 not in the cited passages
- **healthcare_sea**: charts: The quoted source line for this chart point is not present in the source evidence or does ; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence
- **semiconductors_global**: charts: – | numbers: is in the evidence for another metric or entity; is not in the evidence
- **internet_use_comparison**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated unit.; The quoted source row does not support the chart point's entity or category. | numbers: is in the evidence for another metric or entity; is not in the evidence
- **pms_workbook**: charts: The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated measure. | numbers: calculation uses numbers that are not facts in the evidence; is not in the evidence
- **insurance_penetration**: charts: The chart source period falls outside the time window requested by the user. | numbers: input(s) 100 not in the cited passages
- **telecom_5g**: charts: The chart value does not match the quoted source row, period, or named table column. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 17.01 not in the cited passages; input(s) 176.59 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence; row: 12,732,415 not in the evidence; row: 19,430 not in the evidence; row: 941.82 not in the evidence
- **pharma_exports**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not a well-formed number
- **fmcg_rural**: charts: The chart value does not match the quoted source row, period, or named table column. | numbers: has a different unit in the evidence; input(s) 1 not in the cited passages; input(s) 12 not in the cited passages; is in the evidence for another metric or entity
- **real_estate_housing**: charts: The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; input(s) 335476 not in the cited passages; input(s) 82,892, 70,229 not in the cited passages; is in the evidence for another metric or entity; row: 35.5 not in the evidence; row: 36.6 not in the evidence
- **agri_msp**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **india_inflation**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; input(s) 100 not in the cited passages; is in the evidence for another metric or entity
- **us_labour_market**: charts: The chart value does not match the quoted source row, period, or named table column. | numbers: is in the evidence for another metric or entity; is not in the evidence; row: 17.7 not in the evidence; row: 50.5 not in the evidence; row: 72.3 not in the evidence; row: 76.8 not in the evidence
- **china_exports**: charts: The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not in the evidence
- **ai_investment**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 1 not in the cited passages; is in the evidence for another metric or entity
- **india_ipo_market**: charts: The chart source period falls outside the time window requested by the user.; The quoted source line for this chart point is not present in the source evidence or does  | numbers: is in the evidence for another metric or entity; is not a well-formed number
- **mutual_fund_sip**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; is in the evidence for another metric or entity; is not in the evidence; row: 508, 516 not in the evidence; row: 516 not in the evidence
- **steel_production**: charts: The chart source period falls outside the time window requested by the user. | numbers: is in the evidence for another metric or entity
- **oil_prices_india**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 1, 5 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence; row: 75.23 not in the evidence
- **defence_exports**: charts: – | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; is in the evidence for another metric or entity
- **tourism_recovery**: charts: The chart source period falls outside the time window requested by the user.; The chart value does not match the quoted source row, period, or named table column.; The quoted source line for this chart point is not present in the source evidence or does ; The quoted source row does not establish the chart's stated measure. | numbers: calculation uses numbers that are not facts in the evidence; is in the evidence for another metric or entity; is not in the evidence; row: 144 not in the evidence
- **education_outcomes**: charts: The chart source period falls outside the time window requested by the user. | numbers: is in the evidence for another metric or entity; is not in the evidence
- **upi_payments**: charts: The chart source period falls outside the time window requested by the user. | numbers: calculation uses numbers that are not facts in the evidence; has a different unit in the evidence; input(s) 81.9, 77.8 not in the cited passages; is in the evidence for another metric or entity; is not in the evidence
- **auto_sales_ice**: charts: The chart source period falls outside the time window requested by the user.; The quoted source value cannot be tied to a local row or statement. | numbers: calculation uses numbers that are not facts in the evidence; is not in the evidence
- **climate_emissions**: charts: – | numbers: has a different unit in the evidence; is not in the evidence
