---
paths:
  - "**/*.ts"
  - "**/*.html"
  - "**/*.scss"
---

# Angular Conventions

- **Singular folder names** unless the plural changes the meaning: `layout/`, `page/`, not `layouts/`, `pages/`.
- **Every component in its own folder** with its `.ts`, `.html`, `.scss` and `.spec.ts`: `page/landing/landing.ts`.
  Non-component code sits beside the components it serves.
- **CRUD method names say how many** in services that call the API: `createOne`/`createAll`, `findOne`/`findAll`,
  `updateOne`, `deleteOne`/`deleteAll`, never bare `create()`, `find()`, `load()` or `update()`. Methods that aren't plain
  CRUD keep the verb that says what they do (`addMember`, `invite`).
- **View model collections are never optional or `null`**: an API shape declares `members: Member[]`, not
  `members?: Member[]` or `Member[] | null`, so components iterate without null checks. The API sends an empty array.
