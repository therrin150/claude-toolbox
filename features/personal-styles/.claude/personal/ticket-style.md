# Personal Ticket Style Guide

Allen's Jira ticket conventions. The examples are invented, but they keep the shape and density of real tickets. It
overrides the calling skill's defaults for titles, descriptions, acceptance criteria (AC) and QA notes. Mechanics stay
with the skill: Jira fields, ADF conversion, project/label/estimate/epic rules, and the Obsidian doc format.

Issue types: **User Story**, **Defect**, **Epic**. Nothing else. Internal or tech-debt work is still a User Story.

## Voice

Read `~/.claude/personal/voice.md` as well. It is part of this guide, and its "Everywhere" rules apply to every ticket
field.

## Titles

Title Case. Aim for 25–45 characters. Drop articles ("a", "the") and filler prepositions.

| Type | Shape | Examples |
| --- | --- | --- |
| Story | Imperative verb + object, 3–6 words (or a component noun) | Treat SocketTimeoutExceptions as Retryable · Order-Status SQS Client |
| Defect | The symptom | Resubmitting Order Results in 500 Exception · Bulk Order Stuck in Processing |
| Epic | Bare noun naming the capability or service | Partial Refunds · Order-Status Service |
| Spike | `Spike: ` + the question to answer | Spike: Detect In-Flight Resource Changes |

Name a class or mechanism only once the problem has been isolated to it ("Clean Up 204 Handling in
CatalogWebService"). Otherwise, name the outcome.

Existing issues keep their current capitalization unless I ask for a rename.

## Story Description

No headings. Two moves, usually one short paragraph each, **about 72–120 words of prose in total**:

1. **Context and cost.** How the system behaves today, naming the services involved, and why that's a problem.
   Lead with the service or its current behavior as the subject, and vary the opening from story to story.
2. **Directive.** Tell the dev what to change: "Update X so that Y.", "Add …", "Create …", "Remove …". Be firm on
   *what*, suggestive on *how*: "Use `order-sync`'s logic as a reference", "The method may look something like this:".

Add a block only when it does a job. Blocks don't count toward the word target:

- **Endpoint:** `` `POST /path` `` on its own line, then a one-line purpose, then `Request Body: <Model | Null>`, then one
  paragraph of rules (validation → status change → message put on which queue).
- **Steps:** a short lead-in, then numbered imperative lines, for multi-part changes.
- **Examples:** a method signature, a payload or log line, a SQL/DQL query, or property names with current and target
  values.
- **Scope boundary:** what this story deliberately leaves to a sibling story, linked when it exists.
- **References:** related tickets, Confluence API docs, "See the Bruno collection".

**Source detail from `plan.md` when one exists.** Pull in the class, method, endpoint, queue, query or payload the dev
needs in order to start without reading the plan. Present anything the plan hasn't settled as a suggestion. Rationale,
rejected alternatives and the investigation trail stay in the plan, which is local, so never link to it.

**Size alarm.** If the prose can't stay near 120 words without losing something important, or the AC needs more than 5
scenarios, stop. Tell the user the story may be too large and propose a split. Don't pad and don't cut.

- **Shared work is its own story.** If sibling stories would each carry the same work ("whichever lands first builds
  it"), propose that work as a separate story instead of copying it into both.
- **Name the tool, don't repeat its process.** When a referenced skill, runbook or tool already enforces a process
  (rollout steps, dry-runs, recording IDs), say to use it and stop there.
- **New findings go to the user.** A gap, conflict or flaw you notice while drafting that the source doesn't cover gets
  raised with the user as a question, not written into the ticket.

## Defect Description

1. **Symptom.** What was observed, where, and who noticed. Keep the context that explains it right beside it (for example,
   why the data got into that state). Don't hold it back for the cause step or drop it.
2. **Evidence.** A trimmed log, stack trace, or the external system's error. Keep only the lines that identify the
   failure.
3. **Cause**, if known, as a plain causal chain, stated directly.
4. **Fix**, if known, in one or two imperative sentences. If it's undecided, present the options, either in plain prose or
   as an open question in an info panel.
5. **Steps to Reproduce** (numbered) when the repro isn't obvious. They go here, never in the AC. Say so plainly when a
   race condition or data volume makes repro impractical.

The same prose budget and size check as a Story apply (about 120 words). Evidence and steps don't count toward it.

## Epic Description

1–3 paragraphs, no headings, no AC:

- the need or what's wrong today
- what the capability or service is, and that we'll build it
- what it requires (infrastructure, modules, integrations)
- a closing sentence on phasing when relevant

A one-sentence epic is fine when that's all it needs. Add a FAQ panel only to head off an objection you can predict.

## Spike

Title `Spike: …`. The description states the question and what's already known, and ends with a sentence or two defining
the goal. There's no AC.

## Panels

Use Jira panels, not emoji, and only when warranted:

- **info:** definitions, context, open questions, or a FAQ
- **warning:** gotchas (for example Redis eventual consistency) and deploy-order gates ("Only after X is deployed to
  prod …")

## Acceptance Criteria

**Default to GIVEN/WHEN/THEN.** Use 1–5 scenarios: the happy path, the invalid-state path, and the variants that
actually behave differently. Format:

```
**Scenario: Invalid Order Status**
GIVEN a standard (non-DirectShip) order NOT in `PAYMENT_CONFIRMED` status
WHEN the Ship Order endpoint is called for that order
THEN a 400 response is returned with an error message indicating the invalid status
AND the order status is NOT changed
AND a `PICK` message is NOT put onto `fulfillment-worker`'s queue
```

- **Assert only what QA can observe from outside the running app:** HTTP status and message, submission status, a
  message put (or NOT) on a named queue, a log entry, DB rows, Dynatrace counts or metrics, a downstream call made (or
  NOT), and the UI.
- **QA's reach:** dev mostly; beta and prod when needed (sometimes prod is the only way). Bruno, read-only DB
  everywhere, write access in dev/beta, Dynatrace (DQL, metrics, logs) and the AWS Console.
- **Never assert on internals:** no method return values, class behavior, or "`X.isRetryable` returns true".
  **Exception:** a library-only change that no running app exposes. Say that QA verifies it by code review and unit
  tests.
- **Keep GIVEN to the conditions that matter.** A long AND-chain of data setup belongs in the QA comment, not the
  scenario.
- **Say what should happen.** Don't defer to "the steps in the Description" unless that saves significant duplication.
- **Add an "unchanged / unaffected" scenario only** when the change touches a path QA wouldn't otherwise exercise.
  Otherwise, regression covers it.
- **No code-cleanup reminders** ("infrastructure removed"). QA can review the PR.
- **Parentheses clarify conditions:** "a committable resource (permission change or nextEvalDate)".

**Brief statement instead**, only for a truly single-path requirement. Use bullets, written to QA as "you" when natural:

- "A PUT to `/item/list` with an existing item (same `sku`, `warehouseId` and `type`) returns 400, not 500."
- "You see only a WARN entry stating 'No item found in the catalog for this ID', not the ERROR and stack trace."
- Removals and refactors: "X no longer does Y. Instead, it does Z." or "still works as before, but ALSO …".

**Defect AC** is required and usually brief. It states the expected result, ideally at the relevant repro step:
"Resubmitting an order NOT in `PAYMENT_CONFIRMED` to `POST /orders/submit` returns 400 with an appropriate message."

**Epic and Spike:** no AC.

## QA Validation Notes (Jira Comment)

Posted as a comment right after the issue is filed. It never goes in the description or the AC. It's optional, and
it's usually absent.

Write 1–3 bullets, and only when validation needs more than the Bruno requests and the AC already imply. For example:

- "Follow these steps to create the test data: …"
- "Use this DQL to confirm the counter reports in dev: `timeseries sum(orders.fulfillment.stuck), by:{env}`"
- "This can only be validated in prod: check … after the next bulk order completes."

Don't restate or overlap an AC scenario, and don't list the Bruno requests QA already has.

## Examples

These show shape and density, not wording to copy.

**Story, short** (PROJ-2654, *Treat SocketTimeoutExceptions as Retryable*):

> Order-Service's new exception converter expects an HttpStatusCodeException with a status of 5xx (and only that) to be
> retryable at the moment. If a request times out, however, a SocketTimeoutException is thrown instead, which is
> currently being treated as not retryable.
>
> Update the exception converter to examine the cause hierarchy of the given non-HttpStatusCodeException looking for
> SocketTimeoutExceptions (things are implemented so many different ways that the SocketTimeoutException could be
> wrapped by N other exceptions). If it finds one, treat it as retryable. Else, proceed with current exception handling.
>
> **AC:** A request timeout (SocketTimeoutException in the logs) is treated as retryable (as defined in PROJ-169).

**Defect** (PROJ-1769, *NullPointerException from Counts Queries*, trimmed):

> The `ORDER_REPORT_ITEM_COUNTS_QUERY` and `ORDER_REPORT_SHIPMENT_COUNTS_QUERY` in the reporting service result in a
> NullPointerException being thrown if the resulting count is zero, because the return value is being set on a `long`
> field in the model.
>
> Add a COALESCE() to the query to ensure a zero is returned in these cases, preventing the NullPointerException from
> being thrown.
>
> Stack trace from QA: *(trimmed log block)*
>
> **AC:** NullPointerExceptions are no longer thrown when generating order_report data and a given order has no item or
> shipment counts from child order_report records.

**Epic, short** (PROJ-1726, *Reporting Read-Only RDS Instance*):

> We need to add a read-only instance to the reporting service's RDS cluster, configure the additional read-only
> datasource in code, and migrate our lookups to use the read-only instance, all in an effort to better spread the load
> on the database and improve overall performance.
