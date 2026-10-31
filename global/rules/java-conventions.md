---
paths:
  - "**/*.java"
  - "**/pom.xml"
---

# Java Conventions

Allen's conventions for his own Java projects. A project's `*-java-conventions` rule adds its versions, libraries and
anything it decides differently.

## Structure

- **Layered packages under the project package** (`com.allensudweeks.<project>`): `config` (`config/security`),
  `exception`, `controller`, `model` (`model/domain`, `model/view`), `repository`, `service`, `util`.
- **The application class sits in `com.allensudweeks`**, one level above the project package, so the default component
  scan picks up Allen's shared libraries once he extracts them.
- **Test packages mirror main.**
- **Models**: the entity is `Person`, its API shape `PersonView`. Concrete classes with Lombok; **no records**.
- **Own file for anything shared**: a nested class or interface used outside its own file moves to a file of its own.
- **Names**: `GlobalExceptionHandler`, `{Domain}Controller`, `{Domain}Service`, `{Entity}Repository`. Name a method for
  what it does (`fetchAdministrator`, not `requireAdministrator`).

## Style

- **No comments** unless the code or configuration can't speak for itself: a non-obvious why, never what. No class-level
  Javadoc that restates the name, no comments in YAML or SQL that repeat documentation kept elsewhere.
- **`Optional` for null checks**: `Optional.ofNullable(x).filter(...).map(...)` over `if (x == null || ...)`. Repository
  lookups return `Optional`.
- **Use common utilities, don't reinvent them**: Apache Commons `StringUtils`, `CollectionUtils`, math, file and I/O
  utilities. Another utility library is recommended when it fits, but ask Allen before adding it to the standard set.
- **Break up nested calls.** One step per line with a named local, so a debugger shows each value:
  `String displayName = displayName(userInfo);` then `Person request = Person.builder().displayName(displayName).build();`,
  not `save(Person.builder().displayName(displayName(userInfo)).build())`.
- **Spring Data**: derive queries from the method name; `@Query` only when a name can't say it. `@EntityGraph` to fetch
  a relationship.
- **JPA mapping**: `@Entity` plus a bare `@Table`; don't repeat a table, column or join-column name Spring infers.
  Name one explicitly only when it buys something (`@JoinColumn(name = "person_id")` to a table-named key).
- **No deprecated calls.** Use the replacement even when it isn't an Apache Commons method
  (`Strings.CS.appendIfMissing`, not `StringUtils.appendIfMissing`).
- Constructor injection, never field injection. Imports, not qualified names.

## Tests

- **Unit tests**: `{Class}Test`, JUnit 5, Mockito, AssertJ; mock only collaborators.
- **Integration tests** call the running application from outside with a `RestClient` (`@SpringBootTest` on a random
  port), never `MockMvc`, and don't mock the API layer. Stub only what is outside the app. Tests create and remove their
  own data. Name them for the controller: `MeControllerIT`, or controller and method for a complicated one.
