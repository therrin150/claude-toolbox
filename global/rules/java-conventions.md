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
  `exception`, `controller`, `model` (`model/domain`, `model/view`), `repository`, `service` (`service/converter`),
  `util`.
- **Models and services are split by concept inside their layer**: `model/domain/party`, `model/view/party`,
  `service/party`, `service/converter/party`, one subpackage per entity concept (`me`, `person`, `invitation`,
  `party`), holding the entity, its views, its facade service, the services for its operations and its converters.
  Tests mirror the same subpackages.
- **The application class sits in `com.allensudweeks`**, one level above the project package, so the default component
  scan picks up Allen's shared libraries once he extracts them.
- **Test packages mirror main.**
- **Models**: the entity is `Person`, its API shape `PersonView`. Concrete classes with Lombok; **no records**.
- **View model collections and maps default to an empty instance**, never `null`: initialize the field
  (`private List<UUID> partyIds = new ArrayList<>();`) and annotate it `@JsonSetter(nulls = Nulls.AS_EMPTY)`, so a
  missing or `null` value deserializes as empty and consumers skip the null checks.
- **Own file for anything shared**: a nested class or interface used outside its own file moves to a file of its own.
- **Controllers are thin pass-throughs**: they hand the request to a service and return what it returns. Validation
  stays in the controller only when it serves no purpose in the service layer; reading a request body, choosing between
  paths, building a view, calling two service methods and everything else belongs in a service (`PersonService`, or a
  smaller dedicated one).
- **Names**: `GlobalExceptionHandler`, `{Domain}Controller`, `{Domain}Service`, `{Entity}Repository`. Name a method for
  what it does (`fetchAdministrator`, not `requireAdministrator`).
- **CRUD method names say how many** (services, controllers and API clients alike): `createOne`/`createAll`,
  `findOne`/`findAll` (overloads for search criteria and pageables), `updateOne`/`updateAll`, `deleteOne`/`deleteAll`,
  never bare `create()`, `find()`, `list()`, `update()` or `delete()`. Methods that aren't plain CRUD keep the verb that
  says what they do (`accept`, `addMember`).
- **A service with many collaborators is split by operation**: a facade that delegates to one service per operation
  (`InvitationService` to `InvitationCreator` and `InvitationAccepter`), each holding only the dependencies it needs.

## Converters

- **All model conversion lives in `*Converter` services** in `service/converter`, so that adopting a conversion library
  later changes one package. Services and controllers inject a converter and call it; they never build a view or an
  entity from another model themselves.
- **`convertOne()` and `convertAll()`** take a domain or view model and return its opposite. `convertAll()` accepts any
  `Collection` and returns a `List`, with an overload taking the collection to build, so no converter is tied to
  `List`. A shared base class (`Converter<S, T>`, and `ContextConverter<S, T, X>` when extra data is needed) provides
  `convertAll()` over `convertOne()`.
- **Converters are self-contained.** They may depend on other converters and on plain beans such as `Clock`, never on
  repositories or other services. Whatever data a conversion needs (a caller's role, the people behind a list of
  memberships) is loaded by the service and handed over, in a context object when it is more than one value.

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
