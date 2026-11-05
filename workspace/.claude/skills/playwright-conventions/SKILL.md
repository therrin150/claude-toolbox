---
name: playwright-conventions
description: Allen's Playwright end-to-end conventions - happy-path smoke tests only, locators by role and label, per-test data through the API, no retries, no pixel diffs, every viewport, short failure output, look specs that aren't regression tests. Read before planning, writing, running or debugging Playwright tests.
user-invocable: false
---

# Playwright Conventions

Allen's conventions for Playwright smoke tests in his own projects. A project's `<prefix>-` rules add its projects,
paths and test users; where they disagree, the project's rule wins.

- **Smoke tests over the core flows' happy paths**, run locally against the full stack, not in CI unless the project
  decides otherwise. Edge cases stay in unit and integration tests. A flow gets a spec when it's core, hard to check by
  hand, or has broken once.
- **Locators by role and label**: `getByRole('button', { name: 'Reconnect' })`, `getByLabel('Title')`. No CSS classes,
  and `data-testid` only where no role can name the element. Never XPath.
- **Per-test data through the API**: each test creates what it needs with a test user's token and unique names, so
  tests don't depend on each other or on a reset database. Never read or change Allen's own local data.
- **One strike for flakes**: `retries: 0`. A test that fails without a code change is fixed at its cause or deleted in
  the same story, never re-run until green.
- **No pixel diffs**: no `toHaveScreenshot`. Fonts and anti-aliasing differ between machines. Assert on roles, text and
  visibility.
- **Every size**: specs run in phone, tablet and desktop projects (plus WebKit where Safari behavior matters, such as
  offline).
- **Short failures**: `--reporter=dot`. Read the first failure's error, then `error-context.md` only if that isn't
  enough. Screenshots and traces are for Allen's trace viewer, never read into context.
- **A look spec is not a regression test.** A project may keep one spec that only gives Claude a signed-in look at
  chosen routes (skipped unless asked for), asserting no sideways scroll and writing each route's aria snapshot and
  screenshot per project and color scheme. Don't extend it with per-screen assertions; those belong in the screen's own
  spec.
