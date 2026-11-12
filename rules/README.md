# Rules

Plain CSVs the parser reads on every run. Edit them, then re-run the parser.

| File | What it controls |
|---|---|
| `workout_types.csv` | Label text at the top of a block (the part before the colon, lowercased) -> workout type + format. `status=open` means Patrick hasn't mapped it to one of his categories yet. |
| `pace_bands.csv` | Pace anchor after the `@` -> threshold band (DT / LT / AnT). Applies only to the threshold family. |
| `inference.csv` | Cutoffs (target pace as % of mile fitness) used to guess the type of unlabeled blocks. |

Decisions recorded here come from Patrick; `status=default` marks a call Claude made that Patrick can override.

Rules that name athletes live in `data/rules/` (git-ignored) so no student names end up in the repo:

- `data/rules/aliases.csv` - workbook spelling/nickname -> Athletic.net athlete ID. Add a row whenever the review file reports an unmatched name.
- `data/rules/athletes.csv` - per-athlete, per-season notes (e.g. alumni training with the group).
- `data/rules/strava_aliases.csv` - Strava display name -> Athletic.net athlete ID, for the weekly mileage exports.
