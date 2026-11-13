---
name: java-conventions
description: Allen's Java conventions for his own projects - versions, package layout, naming (CRUD counts, full names, Domain/View/Request models), converters, coding style, formatting with Spotless, structured logging with the SLF4J fluent API. Read before planning, writing or reviewing Java code. Tests are java-testing-conventions; Spring specifics are spring-boot-conventions.
user-invocable: false
---

# Java Conventions

Allen's conventions for his own Java projects. A project's `<prefix>-java-conventions` rule adds its modules, libraries
and anything it decides differently; where the two disagree, the project's rule wins.

## Versions

- Java 25 (Amazon Corretto) unless the project's root `pom.xml` says otherwise (`<java.version>`).
- Maven, with the root `pom.xml` as the parent of every module and Maven run from the repo root. Shared plugin
  config (Spotless on every module; the compiler's Lombok processor and JaCoCo in `pluginManagement`, applied by the
  modules that need them) and the modules' versions live there.

## Common Libraries

- **Lombok**: `@Getter`, `@Setter`, `@Builder`, `@Slf4j`, `@Data`.
- **Validation**: `@Valid`, `@NotNull`, `@Size`, and so on.
- **Apache Commons**: `StringUtils`, `CollectionUtils`, math, file and I/O utilities over hand-rolled ones. Another
  utility library is fine when it fits, but ask Allen before adding it to the standard set.

## Structure

- **Layered packages under the project package** (`com.allensudweeks.<project>`): `config` (`config/security`),
  `exception`, `controller`, `model` (`model/domain`, `model/view`), `repository`, `service` (`service/converter`),
  `util`.
- **Models and services are split by concept inside their layer**: `model/domain/party`, `model/view/party`,
  `service/party`, `service/converter/party`, one subpackage per entity concept, holding the entity, its views, its
  facade service, the services for its operations and its converters.
- **The application class sits in `com.allensudweeks`**, one level above the project package, so the default component
  scan picks up Allen's shared libraries once he extracts them.

  ```
  com.allensudweeks/
  ├── <Project>Application.java
  └── <project>/
      ├── config/security/
      ├── controller/
      ├── exception/                     # GlobalExceptionHandler and the exceptions it answers
      ├── model/domain/<concept>/        # entities
      ├── model/view/<concept>/          # what the API sends and receives, external payloads
      ├── repository/
      ├── service/<concept>/             # a facade per concept and one service per major operation
      ├── service/converter/<concept>/   # *Converter services: every model-to-model conversion
      └── util/
  ```

- **Test packages mirror main.**
- **Own file for anything shared**: a nested class or interface used outside its own file moves to a file of its own.
- **A service with many collaborators is split by operation**: a facade that delegates to one service per operation
  (`InvitationService` to `InvitationCreator` and `InvitationAccepter`), each holding only the dependencies it needs.

## Naming

- **Classes**: `GlobalExceptionHandler`, `{Domain}Controller`, `{Domain}Service`, `{Entity}Repository`.
- **Models**: the entity is `Person`, its API shape `PersonView`, a request body `{Domain}Request`. Concrete classes
  with Lombok; **no records**.
- **Full names, never abbreviations**, in classes, fields, tables, columns and paths: `Invitation`, `invitation`,
  `/api/invitation`, not `Invite`. The UI can be casual ("invite").
- **CRUD method names say how many** (services, controllers and API clients alike): `createOne`/`createAll`,
  `findOne`/`findAll` (overloads for search criteria and pageables), `updateOne`/`updateAll`, `deleteOne`/`deleteAll`,
  never bare `create()`, `find()`, `list()`, `update()` or `delete()`. Methods that aren't plain CRUD keep the verb that
  says what they do (`accept`, `addMember`).
- **Name a method for what it does** (`fetchAdministrator`, not `requireAdministrator`).

## Models

- **View model collections and maps default to an empty instance**, never `null`: initialize the field
  (`private List<UUID> partyIds = new ArrayList<>();`) and annotate it `@JsonSetter(nulls = Nulls.AS_EMPTY)`, so a
  missing or `null` value deserializes as empty and consumers skip the null checks.

## Converters

- **All model conversion lives in `*Converter` services** in `service/converter`, so that adopting a conversion library
  later changes one package. Services and controllers inject a converter and call it; they never build a view or an
  entity from another model themselves.
- **`convertOne()` and `convertAll()`** take a domain or view model and return its opposite. `convertAll()` accepts any
  `Collection` and returns a `List`, with an overload taking the collection to build, so no converter is tied to
  `List`. A shared base class (`Converter<S, T>`, and `ContextConverter<S, T, X>` when extra data is needed) provides
  `convertAll()` over `convertOne()`.
- **Converters are self-contained.** They may depend on other converters and on plain beans such as `Clock`, never on
  repositories or other services. Whatever data a conversion needs is loaded by the service and handed over, in a
  context object when it is more than one value.

## Style

- **No comments** unless the code or configuration can't speak for itself: a non-obvious why, never what. No class-level
  Javadoc that restates the name, no comments in YAML or SQL that repeat documentation kept elsewhere.
- **`Optional` for null checks**: `Optional.ofNullable(x).filter(...).map(...)` over `if (x == null || ...)`. Repository
  lookups return `Optional`.
- **Break up nested calls.** One step per line with a named local, so a debugger shows each value:
  `String displayName = displayName(userInfo);` then `Person request = Person.builder().displayName(displayName).build();`,
  not `save(Person.builder().displayName(displayName(userInfo)).build())`.
- **No deprecated calls.** Use the replacement even when it isn't an Apache Commons method
  (`Strings.CS.appendIfMissing`, not `StringUtils.appendIfMissing`).
- Constructor injection, never field injection. Imports, not qualified names. Lombok to cut boilerplate.
- Create or update unit tests alongside every source change (java-testing-conventions).

## Formatting

- google-java-format, AOSP style (4-space indents, 100 columns), through Spotless. `mvn spotless:apply` from the repo
  root fixes every module; `spotless:check` runs in `mvn verify`, so CI fails on drift.
- IntelliJ uses the google-java-format plugin, configured in a committed `.idea/google-java-format.xml`, so Reformat
  matches CI. Run `spotless:apply` before every review.

## Structured Logging

Use the SLF4J 2.x fluent API:

```java
log.atInfo()
   .setMessage("Recipe imported")       // first in the chain
   .addKeyValue("recipe_id", recipeId)
   .addKeyValue("duration_ms", durationMs)
   .setCause(e)                          // not a trailing exception argument
   .log();
```

- `setMessage()` first, then fields, then `setCause()`, then `.log()`.
- Prefer `addKeyValue()` over `{}` placeholders.
- `@Slf4j`, never `System.out.println`.
- **snake_case keys**; durations are `duration_ms`.
- Never use `timestamp`, `logger`, `level`, `thread`, `message`, `method` as keys (they collide with the JSON encoder's
  own fields).
- Never pass a literal `null` to `addKeyValue` (it binds to the `Supplier` overload and throws). A possibly-null
  variable is fine.
- Log identifiers and scalars, not domain objects. **Never log personal data**: names, emails, user content.
- Use a supplier for anything expensive: `addKeyValue("k", () -> compute())`.

## Code Quality

- **JaCoCo** coverage reports in `target/site/jacoco/`; exclude `*Application.java`, `*Config.java`, `*Exception.java`.
