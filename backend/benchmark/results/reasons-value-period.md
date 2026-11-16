# Why numbers are removed and charts rejected (all benchmark prompts, current code)

## Numbers removed by the number policy: 226 across 30 prompts

| Reason | Count | Prompts affected |
|---|---:|---:|
| is in the evidence for another metric or entity | 86 | 22 |
| is not in the evidence | 52 | 16 |
| requested calculation uses numbers that are not facts in the evidence | 25 | 16 |
| has a different unit in the evidence | 21 | 10 |
| calculation input(s) N not in the cited passages | 17 | 10 |
| table row: N not in the evidence | 11 | 4 |
| calculation does not reproduce N | 8 | 2 |
| calculation input(s) N N not in the cited passages | 3 | 3 |
| is not a well-formed number | 3 | 3 |

## Chart candidates rejected before writing: 133 across 30 prompts

| Reason | Count | Prompts affected |
|---|---:|---:|
| The chart source period falls outside the time window requested by the user. | 79 | 19 |
| The quoted source line for this chart point is not present in the source evidence or does  | 21 | 13 |
| The chart value does not match the quoted source row, period, or named table column. | 16 | 10 |
| The quoted source value cannot be tied to a local row or statement. | 7 | 3 |
| The quoted source row does not establish the chart's stated measure. | 5 | 2 |
| The quoted source row does not support the chart point's entity or category. | 2 | 2 |
| The heatmap cell is not supported by the cited source row and period column. | 2 | 1 |
| The quoted source row does not establish the chart's stated unit. | 1 | 1 |

