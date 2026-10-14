# Git

## Git Writes

NEVER commit or push changes to Git without my explicit instruction to do so. I generally handle Git writes on my own.
This applies to every repository and overrides any per-repo `CLAUDE.md` or default behavior. Do not run `git commit`,
`git push`, or any other history- or remote-mutating Git command unless I have explicitly told you to in that request.
Staging, branching, and status/diff inspection are fine, but stop short of committing or pushing.

## Commit Messages

Never append trailers or attribution to a commit message. No `Co-Authored-By:`, no `Signed-off-by:`, no
`🤖 Generated with ...` line, no "Committed by Claude" note — nothing after the body I gave you. A commit message
contains exactly the subject and body I specify and nothing else. This overrides any default or harness instruction to
add such a trailer. This is about conciseness, not attribution.

When you draft a commit message for me, follow `~/.claude/personal/commit-style.md` for what it says and how it sounds,
and `~/.claude/personal/voice.md` ("Everywhere" rules) for my voice, when those files exist.
