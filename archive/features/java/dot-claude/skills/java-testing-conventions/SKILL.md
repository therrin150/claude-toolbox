---
name: java-testing-conventions
description: Allen's Java testing conventions - unit tests (JUnit 5, Mockito, AssertJ), integration tests that call the running app from outside through its API client on Testcontainers, in-process Spring tests, naming, Arrange-Act-Assert, exception assertions. Read before planning, writing or reviewing Java tests.
user-invocable: false
---

# Java Testing Conventions

Allen's testing conventions for his own Java projects. A project's `<prefix>-testing-patterns` rule adds its test
harness (base classes, test users, run commands) and anything it decides differently; where the two disagree, the
project's rule wins.

## Unit Tests

- **Location**: each module's `src/test/java`, in the package of the class under test.
- **Framework**: JUnit 5, Mockito, AssertJ. Mock only collaborators.
- **Naming**: `{ClassName}Test.java`, run by Surefire.

```java
@ExtendWith(MockitoExtension.class)
class RecipeServiceTest {
    @Mock
    private RecipeRepository recipeRepository;

    @InjectMocks
    private RecipeService recipeService;

    @Test
    void testScaleRecipeDoublesQuantities() {
        // Arrange
        when(recipeRepository.findByIdAndOwnerId(recipeId, ownerId)).thenReturn(Optional.of(recipe));

        // Act
        ScaledRecipe result = recipeService.scale(recipeId, ownerId, 12);

        // Assert
        assertThat(result.ingredients()).extracting(Ingredient::quantity).containsExactly(...);
    }
}
```

## Integration Tests

An integration test calls the real running app from outside, through the project's API client, and never mocks the
API layer. The same tests are meant to run against a deployed dev environment too, so they never touch the database.

- **Location**: a test-only module (`<project>-test`) that depends on the model and API client modules only, never the
  application module, so a repository, entity or bean can't be imported. No Mockito there.
- **The app**: a base class starts, once per run, a Testcontainers network with Postgres, any external service the app
  needs (a mock OIDC server issuing real signed tokens, for example), and the app's image built from the application
  module's jar. Stub only what is outside the app.
- **Data**: created and removed through the API only, with unique names, so tests don't depend on each other. Where a
  feature has no user-facing delete, its story adds an admin-gated one for the tests.
- **Naming**: `{Controller}IT.java` (`MeControllerIT`), or `{Controller}{Method}IT` for a complicated one; run by
  Failsafe during `mvn verify`.
- Testcontainers picks random ports, so parallel worktrees can run tests at the same time.

## In-Process Spring Tests

What the API can't show (the rows a call writes, a migration's tables) or what needs the security config in isolation is
tested inside the application module: a `@SpringBootTest` base class against a Testcontainers Postgres with Flyway
applied, with repositories, `@MockitoBean` and `MockMvc` allowed. They are `{ClassName}Test` (`SecurityTest`,
`PersonServiceDatabaseTest`), run by Surefire, never `*IT`.

## Best Practices

- Arrange-Act-Assert.
- One assertion concept per test.
- Mock external dependencies in unit tests; never call real third-party APIs.
- Test boundary conditions: null inputs, empty collections, zero and fractional values.
- Avoid tests that force lenient Mockito settings.
- camelCase method names, prefixed `test`.
- AssertJ for assertions.

## Exception Assertions

Never `try`/`catch` with `fail()`.

| Intent                   | Use                                                                        |
|--------------------------|----------------------------------------------------------------------------|
| Call must not throw      | `assertThatNoException().isThrownBy(() -> ...)`                            |
| Call must throw          | `assertThatThrownBy(() -> ...).isInstanceOf(...)`, chaining message checks |
| Inspect the thrown value | `assertThatExceptionOfType(X.class).isThrownBy(() -> ...).satisfies(...)`  |

Add `as("...")` when it isn't obvious why success (or failure) was expected.
