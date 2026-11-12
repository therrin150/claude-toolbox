# Session Types

When the user opens a session by describing one of these kinds of work, start its entry skill with no slash command
needed: invoke it with the Skill tool first, passing their words as the arguments.

| The user opens with | Start |
|---|---|
| "let's design X", "how should we handle X", "plan X", "think through X", a change to the toolbox itself | `discussion` |
| "this alert fired", "why is X restricted", "look into this production problem" | `investigate` |
| "work ABC-12", "implement ABC-12", "do this story", a plan or draft path to build, or an ad-hoc change worth its own branch and review | `work-task` |

- Design, planning and workflow optimization are all `discussion`: one problem, one discussion folder. It ends in a
  plan, and in tickets only when the user asks.
- Questions, lookups and trivial edits (a typo, a one-line fix) need no flow, so skip the skill and just do them.
- A single stage of implementation (run the checks, draft the commit message) names its own skill; use that one rather
  than `work-task`.
- When two types fit (a design that turns into building, an investigation that finds a bug), finish the first and
  hand the result to the next: the discussion's findings are the next flow's input.
