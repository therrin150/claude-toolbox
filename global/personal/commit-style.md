# Personal Commit Message Style Guide

Allen's commit message conventions, drawn from his own commits written before he used any commit skill (June 2025 to
August 2026). The examples are invented, but they keep the shape and density of the real ones. It overrides the calling
skill's defaults for what a subject and body say, how long they run and how they sound. Mechanics stay with the skill:
the Jira key, a required body (it becomes the PR description), one message per repo, unwrapped paragraphs, and no
trailers.

## Voice

Read `~/.claude/personal/voice.md` as well. It is part of this guide, and its "Everywhere" rules apply to the subject
and body, with one exception for commits (see "Tone").

## Subject

`<KEY> <phrase>`. Lowercase after the key, except names that carry their own case: acronyms (`SQS`, `DTO`, `TTL`,
`NPE`), products (Jackson, Dynatrace, Redis) and class or enum names as spelled (`OrderHydrator`, `NOT_SHIPPABLE`).
No backticks and no period.

- **Length:** 2–6 words after the key, usually 3. About 25–50 characters in all.
- **Two shapes,** roughly two to one:
  - imperative verb + object: `fix duplicate lookup call`, `stop swallowing ingest failures`, `remove legacy-profiling`
  - a noun phrase naming the change: `hydrator overhaul`, `rest client config`, `order status race condition`
- **Name what changed,** concretely. A class, module or mechanism is fine in the subject when it IS the change
  (`bearer token interceptor`, `add worker-daemon module`). Otherwise name the behavior (`skip preprocessing for
  directShip`).
- **Consumer-side bumps** read `update dependencies` or `update to latest order-status`.
- **Two stories in one commit:** both keys, then a slash joining the two topics: `PROJ-4025 PROJ-4046 java
  stack/fulfillment`.

## Body

### Length

Default to one paragraph of 1–2 sentences, about 15–45 words, and start every draft there. Go longer only when a
reviewer couldn't follow the diff without the extra context: a design change, a tricky fix or a decision someone may
question gets 2–4 paragraphs, up to about 250 words. The number of changes is never a reason to go longer. A story
with five small changes still gets one paragraph that summarizes them by kind, since the diff and the Jira story carry
the detail. Never pad a small change to look thorough.

### Shapes

Pick the one that fits the change:

1. **Imperative lead.** The opening sentence restates the subject with its reason: "Stop swallowing failures when
   calling `billing-ingest`, which is contributing to our stuck order problem." Further changes follow as short
   imperative sentences, one per change: "Upgrade to Jackson 3. Collapse validation properties into single
   `application.yml`. Replace lingering `LegacyClient` usage with `CatalogRestClient`."
2. **Narrative.** For a design choice or a trade-off: what was needed, the options, what was chosen and why. "I opted
   to create `PlatformRestClientConfig` instead, to avoid any future breaking API changes."
3. **That ^.** When the subject says everything, the body can be just "That ^." or "That ^, and …" plus the one extra
   thing.

### What Goes In

Everything below is allowed when the change needs it, not a checklist to fill. Each item is a candidate for a sentence,
and most messages use only the first one or two.

- **The why,** nearly always. A lead sentence, or a clause with "because", "so that" or "in order to". Leave it out
  rather than invent one.
- **The names that matter,** in backticks: the central classes, services, queues, enum values, tables, endpoints and
  properties. Short service names (`predict`, `gatekeeper`, `task-manager`) are fine. Don't enumerate every file
  touched.
- **Ancillary work** as one terse trailing sentence: "Update `rest-client` and Bruno files.", "Update dependencies.",
  "Sonar fixes."
- **An unrelated fix** that rode along gets its own paragraph, opening with "Also": "Also found that we were setting
  the wrong resource type on the predict message."
- **A merge or deploy prerequisite,** in a paragraph opening "Note that": a deploy order, a merge hold, a local rebuild
  step. Only what merging or deploying this commit safely depends on (see "What Stays Out").
- **Tests** by what they cover: "Add a validation test to exercise the `/evaluate` endpoint." Say where something was
  tested by hand only when the change is risky and the reviewer would want to know ("Tested this locally in
  `assemble-legal`, `predict`, and `person`"). Never report a test run's pass/fail.
- **Numbers** when they're the reason, such as the capacity math behind a setting.
- **Credit and decisions:** "Per Sam, …", "Per discussion, …", "After discussing with Sam, …", "as requested by
  Jordan".
- **An invitation to push back** when the change makes a call another dev owns: "See inline comments, and please let
  me know if I'm wrong."

### What Stays Out

These win over anything above, including the examples.

- **No history or incident detail.** No account of how the code behaved before or what went wrong, and no dates,
  counts, services or symptoms from the incident or investigation that prompted the story. The body says what the
  change does now and why, in present terms. The story and the vault keep the record.
- **No rollout consequences or follow-up.** Nothing about what dashboards, alerts or failure rates will show once it
  ships, what to check before consumers pick it up, work left for later, or other to-dos. No "Note that" paragraph for
  any of these.

## Tone

- **First person is normal in a commit body,** an exception to `voice.md`. Use "I" for Allen's own decisions,
  attempts and findings ("I tried to", "I opted for", "I looked at X, but", "I think this'll be more useful"), and "we"
  for the team and the system.
- **Own a mistake plainly and move on,** in a few words ("My bad."), without recounting what went wrong.
- **Be candid about confidence.** "should", "hopefully" and "I'm pretty sure" when that's the truth. State it flatly
  when it's verified.
- **Wry asides are welcome in moderation:** a trailing ellipsis on an exasperated aside, or a dry parenthetical. One
  per message at most, and never in the subject.
- **Plain prose.** No headings, no bullet lists (a short numbered list only for steps that happen in sequence), no
  bold, no em dashes. A caveat is a plain "Note that", not a `**Note:**` label.

## Batches Across Repos

- **The library repo carries the full explanation.** Consumers say what they picked up and why they needed it: "Update
  to latest `platform-common` with the interceptor change."
- **The same change in several repos** gets the same or a near-identical body. Add a sentence only where one repo
  differs: "Also had to deal with `inventory`'s `-common` modules no longer existing."

## Examples

These show shape and density, not wording to copy.

**Short** (`PROJ-4190 stop swallowing ingest failures`):

> Stop swallowing failures when calling `billing-ingest`, which is contributing to our stuck order problem.

**A cleanup story with several changes** (`PROJ-4584 clean up client wrappers`):

> Remove the unused caching, convenience and helper wrappers around our REST clients, and fix `findOrdersByCustomerIds`
> sending its wrapper instead of the search spec. Deprecate `deleteHoldDate` and `getShipmentChildren` to match the
> catalog and shipping APIs.

**Several small changes** (`PROJ-3655 jackson 3 upgrade`, order-reports):

> Upgrade to Jackson 3. Collapse validation properties into single `application.yml`. Replace lingering `LegacyClient`
> usage with `CatalogRestClient`.

**A decision** (`PROJ-4050 rest client config`):

> I needed to make the connection pool configurable to support the `fulfillment-worker` overhaul. It would have required
> introducing a breaking change to the `build()` API that all our client implementations use, so I opted to create
> `PlatformRestClientConfig` instead, to avoid any future breaking API changes. The configurable properties can be
> listed in the config object, and `PlatformRestClient` updated to populate them from the client's configuration key.
>
> I looked at using `@ConfigurationProperties` to build the config object automatically, but the dynamic key makes that
> real tricky. Leaving things mostly as-was for now.

**Longer, with a design reason** (`PROJ-1609 migrate summary data to redis`):

> Due to hot partition throttling on the reporting table under heavy load, we decided to move the summary data, which
> represents a fraction of the report data and is pretty much just incremented counts, out of DynamoDB and into Redis.
> I've applied the same 90-day TTL to the new keys in Redis, matching the 90-day TTL for the remaining report data in
> DynamoDB.
>
> See `OrderSummaryCacheRepository` for the details on this, which replaces the DynamoDB `OrderSummaryRepository`. I
> tried to do it as "drop-in-replacement" as possible, but this also ended up cleaning up quite a bit of related code.

**A mistake** (`PROJ-3915 disable legacy tasks in prod`):

> Disable the legacy tasks in prod, too. My bad.

**A consumer bump** (`PROJ-4109 update dependencies`):

> Update to latest `platform-common` with the interceptor change.
