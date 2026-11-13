---
name: bruno-api-collections
description: Allen's Bruno API collection conventions - the bruno/ folder layout, collection-level OAuth2 auth, the .bru file format and its rules, environment variables, and updating requests in the same commit as a controller endpoint. Read before adding, changing or removing a REST endpoint, or editing a .bru file.
user-invocable: false
---

# Bruno API Collections

When creating or modifying REST controller endpoints, create or update the matching Bruno `.bru` files in the same
commit. A project's `<prefix>-bruno-api-collections` rule adds its environments, identity provider and unattended-run
setup.

## Collection Location

The `bruno/` folder sits at the project root, never inside the application's module folder.

```
bruno/<project>/
├── bruno.json              # Bruno config (name, version)
├── collection.bru          # Collection-level headers and auth
├── environments/
│   ├── local.bru           # the API run from the IDE
│   └── prod.bru            # production
├── {Domain}/               # Folder per domain/resource type
│   ├── folder.bru          # Folder metadata
│   ├── Add One.bru
│   ├── Find One.bru
│   └── ...
└── ...
```

Folders are organized by domain entity or resource type (`Recipe/`, `Plan/`, `Invitation/`).

## Auth

- Auth lives in `collection.bru`, and requests inherit it (`auth: inherit`); requests that need no token set
  `auth: none`.
- For an OIDC provider, use collection-level OAuth2 (authorization code with PKCE, a public client, no client secret).
  Non-secret values (domain, client id, audience) are `vars` in each environment file.
- Every request carries an `assert` on its status, so a run fails on a wrong answer; add one with each new request.
- Never commit a real token. A fallback bearer token is `vars:secret [ accessToken ]`.

## .bru File Format

Bruno uses a custom DSL. Each request file has these sections.

### GET request (no body)

```
meta {
  name: Find One
  type: http
  seq: 1
}

get {
  url: {{hostApp}}/api/recipe/:id
  body: none
  auth: inherit
}

params:path {
  id: 42
}
```

### POST request (with JSON body)

```
meta {
  name: Add One
  type: http
  seq: 1
}

post {
  url: {{hostApp}}/api/recipe
  body: json
  auth: inherit
}

body:json {
  {
    "title": "Sheet Pan Fajitas",
    "servings": 6
  }
}
```

### DELETE request (with query and path params)

```
meta {
  name: Delete One
  type: http
  seq: 3
}

delete {
  url: {{hostApp}}/api/recipe/:id?permanent=false
  body: none
  auth: inherit
}

params:query {
  permanent: false
}

params:path {
  id: 42
}
```

### Folder metadata (folder.bru)

```
meta {
  name: Recipe
}
```

## Key Format Rules

- The HTTP method block (`get`, `post`, `put`, `delete`) uses the **lowercase** method name.
- URLs use `{{variableName}}` for host interpolation, defined in `environments/*.bru`.
- Path parameters use `:paramName` in the URL and are defined in `params:path {}`.
- Query parameters go in `params:query {}`.
- `body: json` or `body: none` in the method block, with content in `body:json {}` if applicable.
- `auth: inherit` so requests use the collection's auth.
- `seq` in `meta` controls display order within a folder.
- Descriptive file names that match the endpoint's method: `Add One.bru`, `Find One.bru`, `Search.bru`.

## Environment Variables

```
vars {
  hostApp: http://localhost:8080
}
vars:secret [
  accessToken
]
```

## When a Controller Endpoint Changes

1. **New endpoint**: create a `.bru` file in the matching domain folder; create the folder with a `folder.bru` if
   needed.
2. **Changed URL path**: update the `url:` field.
3. **Changed request body**: update `body:json {}` with an example matching the new DTO.
4. **Changed parameters**: update `params:path {}` or `params:query {}`.
5. **Removed endpoint**: delete the `.bru` file.
6. **New domain/resource type**: new folder with a `folder.bru`, requests inside it.
