---
name: angular-conventions
description: Allen's Angular and TypeScript conventions - singular folders and a folder per component, strict types (explicit return types, no any, no non-null assertions), CRUD-count service names, API shapes without optional collections, Prettier and angular-eslint, Vitest unit tests, Material theming and accessibility. Read before planning, writing or reviewing Angular, TypeScript, templates or styles.
user-invocable: false
---

# Angular Conventions

Allen's conventions for his own Angular front ends. A project's `<prefix>-` rules add its style guide, shared components
and anything it decides differently; where they disagree, the project's rule wins.

## Folders

- **Singular folder names** unless the plural changes the meaning: `layout/`, `page/`, not `layouts/`, `pages/`.
- **Every component in its own folder** with its `.ts`, `.html`, `.scss` and `.spec.ts`: `page/landing/landing.ts`,
  `layout/app-layout/app-layout.ts`. Non-component code (a guard, a service, a constants file) sits beside the
  components it serves, in the feature's folder.

## Types Are Required

Allen relies on types on arguments and return values; never trade them for brevity.

- `tsconfig.json` keeps Angular's `strict: true` and `strictTemplates: true`. Never loosen a strict flag to make code
  compile.
- **Parameters** are always typed, explicitly or by inference that `noImplicitAny` accepts.
- **Return types** are explicit on every function and method, enforced by
  `@typescript-eslint/explicit-function-return-type`. Inline callbacks (`.map(r => r.id)`) are exempt.
- **No `any`**: `@typescript-eslint/no-explicit-any` is an error. Use `unknown` and narrow it, or write the type.
- No non-null assertions (`!`) to silence the compiler; handle the `null`.
- Model API payloads as types, never as untyped `object` or `Record<string, any>`.

## Services and Models

- **CRUD method names say how many** in services that call the API: `createOne`/`createAll`, `findOne`/`findAll`,
  `updateOne`, `deleteOne`/`deleteAll`, never bare `create()`, `find()`, `load()` or `update()`. Methods that aren't
  plain CRUD keep the verb that says what they do (`addMember`, `invite`).
- **View model collections are never optional or `null`**: an API shape declares `members: Member[]`, not
  `members?: Member[]` or `Member[] | null`, so components iterate without null checks. The API sends an empty array.

## UI

- **Colors come from the theme.** Angular Material 3 system variables (`--mat-sys-primary`,
  `--mat-sys-surface-container`, …), never hex values in components. Both color schemes must work: check light and dark
  before calling UI done.
- **Phones edit in bottom sheets**, wider screens in dialogs.
- **Accessibility**: 4.5:1 text contrast in both schemes, a visible focus ring, 44px touch targets, and
  `prefers-reduced-motion` respected.

## Formatting and Linting

- Prettier on its defaults (2-space indents) except `printWidth: 100`; angular-eslint lints; CI fails on either. Fix
  locally before review; never disable a rule inline without a comment saying why.

## Unit Tests

- Angular's default test runner (Vitest on jsdom, no browser), `*.spec.ts` beside the code.
- The usual scripts, run from the front end's folder:

```bash
npm ci                 # install from the lockfile
npm run lint           # angular-eslint; fails on any violation
npm run format:check   # Prettier; `npm run format` rewrites
npm test               # one headless run; `npm run test:watch` watches
npm run build          # production; `-- --configuration development` for the other
```
