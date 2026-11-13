---
name: spring-boot-conventions
description: Allen's Spring Boot conventions - thin controllers, REST paths and status codes, global exception handling, Spring Data and JPA mapping (derived queries, lazy fetch, entity graphs, ddl-auto validate), HikariCP defaults, Postgres locally and in tests. Read before planning, writing or reviewing Spring Boot code or application config.
user-invocable: false
---

# Spring Boot Conventions

Allen's conventions for his own Spring Boot projects, on top of java-conventions. A project's `<prefix>-` rules add its
security setup, routing and anything it decides differently; where they disagree, the project's rule wins.

## Libraries

- `spring-boot-starter-parent` as the root parent, at the current major when the project is scaffolded.
- **Spring Web**: `@RestController`, `@Service`, `@Component`, `@Repository`.
- **Spring Data JPA / Hibernate**: `@Entity`, `@Table`, `@Column`, `@Query`.
- **Spring Security OAuth2 Resource Server** when the API takes bearer tokens.
- **Validation** (`spring-boot-starter-validation`).

## Controllers and REST

- **Controllers are thin pass-throughs**: they hand the request to a service and return what it returns. Validation
  stays in the controller only when it serves no purpose in the service layer; reading a request body, choosing between
  paths, building a view, calling two service methods and everything else belongs in a service.
- **REST paths start with `/api` and use singular nouns**: `/api/recipe/{id}`, `/api/party/{id}/plan`, not
  `/api/recipes`.
- **Proper HTTP status codes**: 200, 201, 400, 403, 404, 500.
- **`@RestControllerAdvice` in `GlobalExceptionHandler`** for global exception handling.
- Request bodies carry `@Valid`; a field's `@Size` matches its column's length, so the API answers `400` before the
  database would fail.

## Spring Data and JPA

- **`spring.jpa.hibernate.ddl-auto=validate`**: Hibernate checks the schema against the entities; only Flyway changes it.
- **Derived queries first**: build simple queries from the method name; `@Query` only when a name can't say it.
- **`FetchType.LAZY`** for relationships; fetch eagerly with `@EntityGraph` when needed.
- **`@BatchSize`** for collections that are read in loops.
- **Mapping**: `@Entity` plus a bare `@Table`; Spring derives the table name from the class. Don't repeat a table,
  column or join-column name Spring infers (`displayName` is `display_name`). Name one explicitly only when it buys
  something: the primary key's column (`@Column(name = "person_id")`, database-conventions) and a `@JoinColumn` to a
  table-named key (`@JoinColumn(name = "person_id")`), since Spring would derive `person_person_id`.
- Strings carry `@Column(length = n)` matching the column.
- Enums map to Postgres enum types with `@Enumerated(EnumType.STRING)` plus `@JdbcTypeCode(SqlTypes.NAMED_ENUM)`.

```java
@Entity
@Table
public class MealPlan {
    @Id
    @UuidV7
    @Column(name = "meal_plan_id")
    private UUID id;

    private UUID ownerId;

    @Column(nullable = false, length = 64)
    private String name;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Column(nullable = false)
    private MealPlanKind kind;
}
```

## Connection Pooling (HikariCP)

Start small and size up only when measured; a small host shares its memory with every container:

```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 5
      connection-timeout: 30000   # 30 seconds
      idle-timeout: 600000        # 10 minutes
      max-lifetime: 1800000       # 30 minutes
```

## Local and Tests

- The official `postgres` image, the same major version as production, through Docker Compose locally and
  Testcontainers in tests.
