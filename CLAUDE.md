# Working in this repo

Patrick coaches distance at Monte Vista HS. This repo turns his season
workbooks + Athletic.net results into a training database for a calendar app.
Read README.md first.

- Athlete data lives in `data/` and is git-ignored. Never commit it, never paste
  athlete names or splits into commit messages, issues or PRs.
- Never modify the workbooks. The parser only reads them.
- Decisions belong in `rules/*.csv`, not hard-coded. When Patrick settles a
  question (a label mapping, a pace band, an alias), edit the rule file and re-run.
- After any parser change, run `python -m mvtrain.build` and compare the summary
  counts and `data/build/review.csv` to the previous run before calling it done.
- `type_source` on reps/blocks is `sheet` (Patrick wrote it), `inferred` (guessed
  from target pace vs race fitness), or empty. Never present inferred values as his.
- Patrick prefers short check-ins on long jobs: do a first chunk, report, then continue.
