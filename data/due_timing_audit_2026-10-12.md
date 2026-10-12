# DUE timing audit — 2026-10-12

> Frozen evidence — 2026-10-12; current status: [BACKLOG.md](../BACKLOG.md).

Method: episode active at last sampled pre-entry observation, within previous exit -> entry. Target cadence: 5s; actual gaps are recorded per input.
The pooled current row includes IMSA and WEC; the legacy policy only covers WEC. Compare policy rows within WEC.
Pit visits are not fuel-necessity labels. Withdrawn episodes are separate from episodes with no later observed entry.
This audits calculator decisions; renderer freshness and live acceptance remain separate.

Visits after the calculator finishes are retained as evidence but excluded from timing scores.

Early % uses warned stops; within % uses all eligible stops. Misses are included in that denominator.

| Group | Eligible | Warned | Missed | Mean lead | Median lead | >2L early % | 0–2L / eligible % | No later entry episodes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current | 4842 | 2110 | 2732 | 4.56 | 2.00 | 48.34 | 22.51 | 51 |
| current / series=imsa | 2750 | 1240 | 1510 | 5.07 | 3.00 | 50.48 | 22.33 | 42 |
| current / series=imsa / class=GTD | 807 | 371 | 436 | 6.01 | 3.00 | 59.57 | 18.59 | 10 |
| current / series=imsa / class=GTD / stop_group=first / flag_bucket=caution | 37 | 0 | 37 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTD / stop_group=first / flag_bucket=green | 59 | 0 | 59 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=caution | 289 | 93 | 196 | 10.68 | 8.00 | 75.27 | 7.96 | 0 |
| current / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=green | 422 | 278 | 144 | 4.45 | 3.00 | 54.32 | 30.09 | 10 |
| current / series=imsa / class=GTDPRO | 595 | 282 | 313 | 5.45 | 3.00 | 51.42 | 23.03 | 15 |
| current / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=caution | 24 | 0 | 24 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=green | 41 | 0 | 41 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=caution | 220 | 86 | 134 | 9.66 | 6.00 | 65.12 | 13.64 | 0 |
| current / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=green | 310 | 196 | 114 | 3.61 | 2.00 | 45.41 | 34.52 | 15 |
| current / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| current / series=imsa / class=GTP | 613 | 242 | 371 | 4.81 | 3.00 | 50.41 | 19.58 | 17 |
| current / series=imsa / class=GTP / stop_group=first / flag_bucket=caution | 19 | 0 | 19 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTP / stop_group=first / flag_bucket=green | 49 | 0 | 49 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=caution | 215 | 38 | 177 | 8.05 | 5.00 | 63.16 | 6.51 | 2 |
| current / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=green | 330 | 204 | 126 | 4.20 | 2.00 | 48.04 | 32.12 | 15 |
| current / series=imsa / class=LMP2 | 735 | 345 | 390 | 3.93 | 2.00 | 40.00 | 28.16 | 0 |
| current / series=imsa / class=LMP2 / stop_group=first / flag_bucket=caution | 34 | 0 | 34 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=LMP2 / stop_group=first / flag_bucket=green | 12 | 0 | 12 | — | — | — | 0.00 | 0 |
| current / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=caution | 220 | 58 | 162 | 8.84 | 4.00 | 58.62 | 10.91 | 0 |
| current / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=green | 469 | 287 | 182 | 2.94 | 2.00 | 36.24 | 39.02 | 0 |
| current / series=imsa / flag_bucket=caution | 1058 | 275 | 783 | 9.61 | 5.00 | 66.91 | 8.60 | 2 |
| current / series=imsa / flag_bucket=green | 1692 | 965 | 727 | 3.78 | 2.00 | 45.80 | 30.91 | 40 |
| current / series=imsa / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| current / series=imsa / stop_group=first | 275 | 0 | 275 | — | — | — | 0.00 | 0 |
| current / series=imsa / stop_group=subsequent | 2475 | 1240 | 1235 | 5.07 | 3.00 | 50.48 | 24.81 | 42 |
| current / series=wec | 2092 | 870 | 1222 | 3.83 | 2.00 | 45.29 | 22.75 | 9 |
| current / series=wec / class=HYPERCAR | 999 | 457 | 542 | 4.26 | 2.00 | 43.54 | 25.83 | 5 |
| current / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=caution | 18 | 0 | 18 | — | — | — | 0.00 | 0 |
| current / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=green | 125 | 30 | 95 | 1.47 | 1.00 | 3.33 | 23.20 | 0 |
| current / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| current / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=caution | 126 | 31 | 95 | 9.42 | 6.00 | 100.00 | 0.00 | 0 |
| current / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=green | 697 | 392 | 305 | 4.01 | 2.00 | 41.58 | 32.86 | 5 |
| current / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=other | 17 | 4 | 13 | 9.50 | 9.50 | 100.00 | 0.00 | 0 |
| current / series=wec / class=LMGT3 | 1093 | 413 | 680 | 3.37 | 2.00 | 47.22 | 19.95 | 4 |
| current / series=wec / class=LMGT3 / stop_group=first / flag_bucket=caution | 15 | 0 | 15 | — | — | — | 0.00 | 0 |
| current / series=wec / class=LMGT3 / stop_group=first / flag_bucket=green | 130 | 12 | 118 | 0.33 | 0.00 | 0.00 | 9.23 | 0 |
| current / series=wec / class=LMGT3 / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| current / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=caution | 137 | 35 | 102 | 6.06 | 7.00 | 74.29 | 6.57 | 0 |
| current / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=green | 762 | 365 | 397 | 3.21 | 2.00 | 46.30 | 25.72 | 4 |
| current / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=other | 33 | 1 | 32 | 1.00 | 1.00 | 0.00 | 3.03 | 0 |
| current / series=wec / flag_bucket=caution | 296 | 66 | 230 | 7.64 | 7.00 | 86.36 | 3.04 | 0 |
| current / series=wec / flag_bucket=green | 1714 | 799 | 915 | 3.50 | 2.00 | 41.68 | 27.19 | 9 |
| current / series=wec / flag_bucket=other | 82 | 5 | 77 | 7.80 | 9.00 | 80.00 | 1.22 | 0 |
| current / series=wec / stop_group=first | 320 | 42 | 278 | 1.14 | 1.00 | 2.38 | 12.81 | 0 |
| current / series=wec / stop_group=subsequent | 1772 | 828 | 944 | 3.97 | 2.00 | 47.46 | 24.55 | 9 |
| legacy_stint_same_vet | 2092 | 1141 | 951 | 4.72 | 4.00 | 61.88 | 20.79 | 11 |
| legacy_stint_same_vet / series=wec | 2092 | 1141 | 951 | 4.72 | 4.00 | 61.88 | 20.79 | 11 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR | 999 | 572 | 427 | 5.17 | 4.00 | 67.31 | 18.72 | 6 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=caution | 18 | 0 | 18 | — | — | — | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=green | 125 | 30 | 95 | 1.47 | 1.00 | 3.33 | 23.20 | 0 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=caution | 126 | 33 | 93 | 9.76 | 7.00 | 93.94 | 1.59 | 0 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=green | 697 | 505 | 192 | 5.03 | 4.00 | 69.11 | 22.38 | 6 |
| legacy_stint_same_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=other | 17 | 4 | 13 | 12.75 | 11.50 | 100.00 | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / class=LMGT3 | 1093 | 569 | 524 | 4.27 | 3.00 | 56.41 | 22.69 | 5 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=caution | 15 | 0 | 15 | — | — | — | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=green | 130 | 12 | 118 | 0.33 | 0.00 | 0.00 | 9.23 | 0 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=caution | 137 | 46 | 91 | 6.43 | 9.00 | 67.39 | 10.95 | 0 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=green | 762 | 510 | 252 | 4.17 | 3.00 | 56.67 | 29.00 | 5 |
| legacy_stint_same_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=other | 33 | 1 | 32 | 3.00 | 3.00 | 100.00 | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / flag_bucket=caution | 296 | 79 | 217 | 7.82 | 7.00 | 78.48 | 5.74 | 0 |
| legacy_stint_same_vet / series=wec / flag_bucket=green | 1714 | 1057 | 657 | 4.46 | 3.00 | 60.45 | 24.39 | 11 |
| legacy_stint_same_vet / series=wec / flag_bucket=other | 82 | 5 | 77 | 10.80 | 11.00 | 100.00 | 0.00 | 0 |
| legacy_stint_same_vet / series=wec / stop_group=first | 320 | 42 | 278 | 1.14 | 1.00 | 2.38 | 12.81 | 0 |
| legacy_stint_same_vet / series=wec / stop_group=subsequent | 1772 | 1099 | 673 | 4.86 | 4.00 | 64.15 | 22.23 | 11 |
