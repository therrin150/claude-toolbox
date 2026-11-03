# Done Phase

The final sweep and the usage report, once `publish.py` has set `status=done`. `<bin>` is the scripts folder
(SKILL.md).

## Done

`<worktrees>/<work id>/` must be gone, leftovers included: an IDE can write into a folder after its worktree was
removed. `<bin>/worktrees.py list <work id>` lists nothing once it is; if the folder is still there, run
`<bin>/worktrees.py remove <work id>` once more and report anything it refuses. Then hook `before-done`, and say the
task is done in one line.

## Usage

Last thing, once the task is done: read the `Weekly · all models` window from `get_usage` (leave the weekly flags off
unless `plan.status` is `ok`), run
`<bin>/session_usage.py --task <work id> --weekly-percent <percentUsed> --weekly-resets-at <resetsAt>`,
and show its `markdown` as is. It writes `usage` and `usage_total` to the record and carries its own caveats.
