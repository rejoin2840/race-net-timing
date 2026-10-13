# WEC fresh-energy DUE comparison

> Snapshot as of 2026-10-13; current status: [BACKLOG.md](../BACKLOG.md).

Method: episode active at last sampled pre-entry observation, within previous exit -> entry; same state: fresh energy authority vs shipped additive vs fresh additive. Target cadence: 5s; actual gaps are recorded per input.
Pit visits are not fuel-necessity labels. Withdrawn episodes are separate from episodes with no later observed entry.
This audits calculator decisions; renderer freshness and live acceptance remain separate.

Visits after the calculator finishes are retained as evidence but excluded from timing scores.

Early % uses warned stops; within % uses all eligible stops. Misses are included in that denominator.

| Group | Eligible | Warned | Missed | Mean lead | Median lead | >2L early % | 0–2L / eligible % | No later entry episodes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fresh_additive_vet | 4842 | 2109 | 2733 | 4.56 | 2.00 | 48.32 | 22.51 | 51 |
| fresh_additive_vet / series=imsa | 2750 | 1240 | 1510 | 5.07 | 3.00 | 50.48 | 22.33 | 42 |
| fresh_additive_vet / series=imsa / class=GTD | 807 | 371 | 436 | 6.01 | 3.00 | 59.57 | 18.59 | 10 |
| fresh_additive_vet / series=imsa / class=GTD / stop_group=first / flag_bucket=caution | 37 | 0 | 37 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTD / stop_group=first / flag_bucket=green | 59 | 0 | 59 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=caution | 289 | 93 | 196 | 10.68 | 8.00 | 75.27 | 7.96 | 0 |
| fresh_additive_vet / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=green | 422 | 278 | 144 | 4.45 | 3.00 | 54.32 | 30.09 | 10 |
| fresh_additive_vet / series=imsa / class=GTDPRO | 595 | 282 | 313 | 5.45 | 3.00 | 51.42 | 23.03 | 15 |
| fresh_additive_vet / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=caution | 24 | 0 | 24 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=green | 41 | 0 | 41 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=caution | 220 | 86 | 134 | 9.66 | 6.00 | 65.12 | 13.64 | 0 |
| fresh_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=green | 310 | 196 | 114 | 3.61 | 2.00 | 45.41 | 34.52 | 15 |
| fresh_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| fresh_additive_vet / series=imsa / class=GTP | 613 | 242 | 371 | 4.81 | 3.00 | 50.41 | 19.58 | 17 |
| fresh_additive_vet / series=imsa / class=GTP / stop_group=first / flag_bucket=caution | 19 | 0 | 19 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTP / stop_group=first / flag_bucket=green | 49 | 0 | 49 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=caution | 215 | 38 | 177 | 8.05 | 5.00 | 63.16 | 6.51 | 2 |
| fresh_additive_vet / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=green | 330 | 204 | 126 | 4.20 | 2.00 | 48.04 | 32.12 | 15 |
| fresh_additive_vet / series=imsa / class=LMP2 | 735 | 345 | 390 | 3.93 | 2.00 | 40.00 | 28.16 | 0 |
| fresh_additive_vet / series=imsa / class=LMP2 / stop_group=first / flag_bucket=caution | 34 | 0 | 34 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=LMP2 / stop_group=first / flag_bucket=green | 12 | 0 | 12 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=caution | 220 | 58 | 162 | 8.84 | 4.00 | 58.62 | 10.91 | 0 |
| fresh_additive_vet / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=green | 469 | 287 | 182 | 2.94 | 2.00 | 36.24 | 39.02 | 0 |
| fresh_additive_vet / series=imsa / flag_bucket=caution | 1058 | 275 | 783 | 9.61 | 5.00 | 66.91 | 8.60 | 2 |
| fresh_additive_vet / series=imsa / flag_bucket=green | 1692 | 965 | 727 | 3.78 | 2.00 | 45.80 | 30.91 | 40 |
| fresh_additive_vet / series=imsa / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| fresh_additive_vet / series=imsa / stop_group=first | 275 | 0 | 275 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=imsa / stop_group=subsequent | 2475 | 1240 | 1235 | 5.07 | 3.00 | 50.48 | 24.81 | 42 |
| fresh_additive_vet / series=wec | 2092 | 869 | 1223 | 3.83 | 2.00 | 45.22 | 22.75 | 9 |
| fresh_additive_vet / series=wec / class=HYPERCAR | 999 | 456 | 543 | 4.26 | 2.00 | 43.42 | 25.83 | 5 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=caution | 18 | 0 | 18 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=green | 125 | 29 | 96 | 1.41 | 1.00 | 0.00 | 23.20 | 0 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=caution | 126 | 31 | 95 | 9.42 | 6.00 | 100.00 | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=green | 697 | 392 | 305 | 4.01 | 2.00 | 41.58 | 32.86 | 5 |
| fresh_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=other | 17 | 4 | 13 | 9.50 | 9.50 | 100.00 | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=LMGT3 | 1093 | 413 | 680 | 3.37 | 2.00 | 47.22 | 19.95 | 4 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=caution | 15 | 0 | 15 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=green | 130 | 12 | 118 | 0.33 | 0.00 | 0.00 | 9.23 | 0 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=caution | 137 | 35 | 102 | 6.06 | 7.00 | 74.29 | 6.57 | 0 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=green | 762 | 365 | 397 | 3.21 | 2.00 | 46.30 | 25.72 | 4 |
| fresh_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=other | 33 | 1 | 32 | 1.00 | 1.00 | 0.00 | 3.03 | 0 |
| fresh_additive_vet / series=wec / flag_bucket=caution | 296 | 66 | 230 | 7.64 | 7.00 | 86.36 | 3.04 | 0 |
| fresh_additive_vet / series=wec / flag_bucket=green | 1714 | 798 | 916 | 3.49 | 2.00 | 41.60 | 27.19 | 9 |
| fresh_additive_vet / series=wec / flag_bucket=other | 82 | 5 | 77 | 7.80 | 9.00 | 80.00 | 1.22 | 0 |
| fresh_additive_vet / series=wec / stop_group=first | 320 | 41 | 279 | 1.10 | 1.00 | 0.00 | 12.81 | 0 |
| fresh_additive_vet / series=wec / stop_group=subsequent | 1772 | 828 | 944 | 3.97 | 2.00 | 47.46 | 24.55 | 9 |
| fresh_energy_authority | 4842 | 2059 | 2783 | 4.26 | 2.00 | 44.34 | 23.67 | 42 |
| fresh_energy_authority / series=imsa | 2750 | 1240 | 1510 | 5.07 | 3.00 | 50.48 | 22.33 | 42 |
| fresh_energy_authority / series=imsa / class=GTD | 807 | 371 | 436 | 6.01 | 3.00 | 59.57 | 18.59 | 10 |
| fresh_energy_authority / series=imsa / class=GTD / stop_group=first / flag_bucket=caution | 37 | 0 | 37 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTD / stop_group=first / flag_bucket=green | 59 | 0 | 59 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=caution | 289 | 93 | 196 | 10.68 | 8.00 | 75.27 | 7.96 | 0 |
| fresh_energy_authority / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=green | 422 | 278 | 144 | 4.45 | 3.00 | 54.32 | 30.09 | 10 |
| fresh_energy_authority / series=imsa / class=GTDPRO | 595 | 282 | 313 | 5.45 | 3.00 | 51.42 | 23.03 | 15 |
| fresh_energy_authority / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=caution | 24 | 0 | 24 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=green | 41 | 0 | 41 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=caution | 220 | 86 | 134 | 9.66 | 6.00 | 65.12 | 13.64 | 0 |
| fresh_energy_authority / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=green | 310 | 196 | 114 | 3.61 | 2.00 | 45.41 | 34.52 | 15 |
| fresh_energy_authority / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| fresh_energy_authority / series=imsa / class=GTP | 613 | 242 | 371 | 4.81 | 3.00 | 50.41 | 19.58 | 17 |
| fresh_energy_authority / series=imsa / class=GTP / stop_group=first / flag_bucket=caution | 19 | 0 | 19 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTP / stop_group=first / flag_bucket=green | 49 | 0 | 49 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=caution | 215 | 38 | 177 | 8.05 | 5.00 | 63.16 | 6.51 | 2 |
| fresh_energy_authority / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=green | 330 | 204 | 126 | 4.20 | 2.00 | 48.04 | 32.12 | 15 |
| fresh_energy_authority / series=imsa / class=LMP2 | 735 | 345 | 390 | 3.93 | 2.00 | 40.00 | 28.16 | 0 |
| fresh_energy_authority / series=imsa / class=LMP2 / stop_group=first / flag_bucket=caution | 34 | 0 | 34 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=LMP2 / stop_group=first / flag_bucket=green | 12 | 0 | 12 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=caution | 220 | 58 | 162 | 8.84 | 4.00 | 58.62 | 10.91 | 0 |
| fresh_energy_authority / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=green | 469 | 287 | 182 | 2.94 | 2.00 | 36.24 | 39.02 | 0 |
| fresh_energy_authority / series=imsa / flag_bucket=caution | 1058 | 275 | 783 | 9.61 | 5.00 | 66.91 | 8.60 | 2 |
| fresh_energy_authority / series=imsa / flag_bucket=green | 1692 | 965 | 727 | 3.78 | 2.00 | 45.80 | 30.91 | 40 |
| fresh_energy_authority / series=imsa / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| fresh_energy_authority / series=imsa / stop_group=first | 275 | 0 | 275 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=imsa / stop_group=subsequent | 2475 | 1240 | 1235 | 5.07 | 3.00 | 50.48 | 24.81 | 42 |
| fresh_energy_authority / series=wec | 2092 | 819 | 1273 | 3.04 | 2.00 | 35.04 | 25.43 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR | 999 | 448 | 551 | 3.35 | 2.00 | 34.82 | 29.23 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=caution | 18 | 0 | 18 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=green | 125 | 29 | 96 | 1.41 | 1.00 | 0.00 | 23.20 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=caution | 126 | 31 | 95 | 9.42 | 6.00 | 100.00 | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=green | 697 | 384 | 313 | 2.95 | 2.00 | 31.51 | 37.73 | 0 |
| fresh_energy_authority / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=other | 17 | 4 | 13 | 9.50 | 9.50 | 100.00 | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 | 1093 | 371 | 722 | 2.66 | 2.00 | 35.31 | 21.96 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=first / flag_bucket=caution | 15 | 0 | 15 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=first / flag_bucket=green | 130 | 12 | 118 | 0.33 | 0.00 | 0.00 | 9.23 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=caution | 137 | 35 | 102 | 6.06 | 7.00 | 74.29 | 6.57 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=green | 762 | 323 | 439 | 2.39 | 1.00 | 32.51 | 28.61 | 0 |
| fresh_energy_authority / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=other | 33 | 1 | 32 | 1.00 | 1.00 | 0.00 | 3.03 | 0 |
| fresh_energy_authority / series=wec / flag_bucket=caution | 296 | 66 | 230 | 7.64 | 7.00 | 86.36 | 3.04 | 0 |
| fresh_energy_authority / series=wec / flag_bucket=green | 1714 | 748 | 966 | 2.60 | 2.00 | 30.21 | 30.46 | 0 |
| fresh_energy_authority / series=wec / flag_bucket=other | 82 | 5 | 77 | 7.80 | 9.00 | 80.00 | 1.22 | 0 |
| fresh_energy_authority / series=wec / stop_group=first | 320 | 41 | 279 | 1.10 | 1.00 | 0.00 | 12.81 | 0 |
| fresh_energy_authority / series=wec / stop_group=subsequent | 1772 | 778 | 994 | 3.14 | 2.00 | 36.89 | 27.71 | 0 |
| shipped_additive_vet | 4842 | 2110 | 2732 | 4.56 | 2.00 | 48.34 | 22.51 | 51 |
| shipped_additive_vet / series=imsa | 2750 | 1240 | 1510 | 5.07 | 3.00 | 50.48 | 22.33 | 42 |
| shipped_additive_vet / series=imsa / class=GTD | 807 | 371 | 436 | 6.01 | 3.00 | 59.57 | 18.59 | 10 |
| shipped_additive_vet / series=imsa / class=GTD / stop_group=first / flag_bucket=caution | 37 | 0 | 37 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTD / stop_group=first / flag_bucket=green | 59 | 0 | 59 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=caution | 289 | 93 | 196 | 10.68 | 8.00 | 75.27 | 7.96 | 0 |
| shipped_additive_vet / series=imsa / class=GTD / stop_group=subsequent / flag_bucket=green | 422 | 278 | 144 | 4.45 | 3.00 | 54.32 | 30.09 | 10 |
| shipped_additive_vet / series=imsa / class=GTDPRO | 595 | 282 | 313 | 5.45 | 3.00 | 51.42 | 23.03 | 15 |
| shipped_additive_vet / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=caution | 24 | 0 | 24 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTDPRO / stop_group=first / flag_bucket=green | 41 | 0 | 41 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=caution | 220 | 86 | 134 | 9.66 | 6.00 | 65.12 | 13.64 | 0 |
| shipped_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=green | 310 | 196 | 114 | 3.61 | 2.00 | 45.41 | 34.52 | 15 |
| shipped_additive_vet / series=imsa / class=GTDPRO / stop_group=subsequent / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| shipped_additive_vet / series=imsa / class=GTP | 613 | 242 | 371 | 4.81 | 3.00 | 50.41 | 19.58 | 17 |
| shipped_additive_vet / series=imsa / class=GTP / stop_group=first / flag_bucket=caution | 19 | 0 | 19 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTP / stop_group=first / flag_bucket=green | 49 | 0 | 49 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=caution | 215 | 38 | 177 | 8.05 | 5.00 | 63.16 | 6.51 | 2 |
| shipped_additive_vet / series=imsa / class=GTP / stop_group=subsequent / flag_bucket=green | 330 | 204 | 126 | 4.20 | 2.00 | 48.04 | 32.12 | 15 |
| shipped_additive_vet / series=imsa / class=LMP2 | 735 | 345 | 390 | 3.93 | 2.00 | 40.00 | 28.16 | 0 |
| shipped_additive_vet / series=imsa / class=LMP2 / stop_group=first / flag_bucket=caution | 34 | 0 | 34 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=LMP2 / stop_group=first / flag_bucket=green | 12 | 0 | 12 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=caution | 220 | 58 | 162 | 8.84 | 4.00 | 58.62 | 10.91 | 0 |
| shipped_additive_vet / series=imsa / class=LMP2 / stop_group=subsequent / flag_bucket=green | 469 | 287 | 182 | 2.94 | 2.00 | 36.24 | 39.02 | 0 |
| shipped_additive_vet / series=imsa / flag_bucket=caution | 1058 | 275 | 783 | 9.61 | 5.00 | 66.91 | 8.60 | 2 |
| shipped_additive_vet / series=imsa / flag_bucket=green | 1692 | 965 | 727 | 3.78 | 2.00 | 45.80 | 30.91 | 40 |
| shipped_additive_vet / series=imsa / flag_bucket=other | 0 | 0 | 0 | — | — | — | — | 0 |
| shipped_additive_vet / series=imsa / stop_group=first | 275 | 0 | 275 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=imsa / stop_group=subsequent | 2475 | 1240 | 1235 | 5.07 | 3.00 | 50.48 | 24.81 | 42 |
| shipped_additive_vet / series=wec | 2092 | 870 | 1222 | 3.83 | 2.00 | 45.29 | 22.75 | 9 |
| shipped_additive_vet / series=wec / class=HYPERCAR | 999 | 457 | 542 | 4.26 | 2.00 | 43.54 | 25.83 | 5 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=caution | 18 | 0 | 18 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=green | 125 | 30 | 95 | 1.47 | 1.00 | 3.33 | 23.20 | 0 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=caution | 126 | 31 | 95 | 9.42 | 6.00 | 100.00 | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=green | 697 | 392 | 305 | 4.01 | 2.00 | 41.58 | 32.86 | 5 |
| shipped_additive_vet / series=wec / class=HYPERCAR / stop_group=subsequent / flag_bucket=other | 17 | 4 | 13 | 9.50 | 9.50 | 100.00 | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=LMGT3 | 1093 | 413 | 680 | 3.37 | 2.00 | 47.22 | 19.95 | 4 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=caution | 15 | 0 | 15 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=green | 130 | 12 | 118 | 0.33 | 0.00 | 0.00 | 9.23 | 0 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=first / flag_bucket=other | 16 | 0 | 16 | — | — | — | 0.00 | 0 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=caution | 137 | 35 | 102 | 6.06 | 7.00 | 74.29 | 6.57 | 0 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=green | 762 | 365 | 397 | 3.21 | 2.00 | 46.30 | 25.72 | 4 |
| shipped_additive_vet / series=wec / class=LMGT3 / stop_group=subsequent / flag_bucket=other | 33 | 1 | 32 | 1.00 | 1.00 | 0.00 | 3.03 | 0 |
| shipped_additive_vet / series=wec / flag_bucket=caution | 296 | 66 | 230 | 7.64 | 7.00 | 86.36 | 3.04 | 0 |
| shipped_additive_vet / series=wec / flag_bucket=green | 1714 | 799 | 915 | 3.50 | 2.00 | 41.68 | 27.19 | 9 |
| shipped_additive_vet / series=wec / flag_bucket=other | 82 | 5 | 77 | 7.80 | 9.00 | 80.00 | 1.22 | 0 |
| shipped_additive_vet / series=wec / stop_group=first | 320 | 42 | 278 | 1.14 | 1.00 | 2.38 | 12.81 | 0 |
| shipped_additive_vet / series=wec / stop_group=subsequent | 1772 | 828 | 944 | 3.97 | 2.00 | 47.46 | 24.55 | 9 |

The shipped comparator reproduces every per-stop record from the 2026-10-12
current-policy audit (excluding only the policy label). Fourteen inputs have no
usable WEC VET and are unchanged. Both VET-bearing inputs represent São Paulo
2026, so these are two feed representations of one event, not two independent races.

Paired timely visits gained/lost: Timing71 42/12; Griiip capture 36/10. Pooled WEC
misses rise from 1,222 to 1,273. Timing71 and capture together yield +56 timely visits;
this is a warning-timing improvement with lower overall visit recall. Visits remain
unlabelled for fuel necessity. The freshness-only comparator isolates expiry from
the high-energy veto. In the capture, authority produces 106 warnings, all within
0–2 laps; 82 visits have no sampled warning. Existing 5% and 12s thresholds are
unchanged. Live and additional-event confirmation remain required.
