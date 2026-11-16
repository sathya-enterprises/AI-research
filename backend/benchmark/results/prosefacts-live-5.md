# Live check: prose fact ids (5 prompts, second sample)

Capture map: `benchmark/captures-prosefacts-live.json`. Compared with `factid_compare.py`.

| | original run | table ids only | table + prose ids |
|---|---|---|---|
| ids used by the writer | 0 | 266 | 728 |
| numbers the writer still typed | 1798 | 1063 | 750 |
| sentences removed by the number policy | 60 | 37 | 30 |

Per prompt (table + prose ids): agri_msp 198 ids / 164 typed / 8 removed; telecom_5g 89 / 162 / 7;
mutual_fund_sip 175 / 161 / 12; india_ipo_market 169 / 145 / 2; steel_production 97 / 118 / 1.

All five are WARNING. The earlier IPO "PASS" was a run where the publication audit was unavailable,
so it is not comparable.

## Defects found in the reports, fixed after this run

- "Rs." split into two sentences (10 cases in 4 reports): "a net worth exceeding Rs [S4]. 25 crore [S4]."
  Later report steps split sentences at ". " before a digit. The section writer now writes "Rs 25 crore".
- Currency column units after the value: "11,455 ₹ Cr", "2248 ₹ crore". Now "₹11,455 Cr".

## Not fixed

- steel: "capacity of 117.4 and produced 55.6 of crude steel". The source table has no unit.
- IPO: the writer turned "not exceeding Rs 10 crores" into "exceeding Rs 10 crore". The inserted values are
  right, but the wording around them is wrong. The number check does not read negations.
