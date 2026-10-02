# Detect Dependencies Heuristics

## What Success Looks Like

The owner learns exactly which tests silently depend on each other — shared mutable fixtures, shared globals, order-dependence, shared live app or data state — before that coupling causes a cascade of unrelated failures. Each finding names the specific shared resource, the tests coupled through it, and a risk level. This is the single highest-impact detector: in the reference engagement this root-cause family accounted for nearly half (47%) of all false-fails, so err toward thoroughness here over the other detectors.

## Approach

Explore the target project's graph rather than guessing. `code-graph query variables` (module-level state and class fields), `code-graph query by-decorator --param decorator=fixture` (or `BeforeAll`, `BeforeClass`, `beforeAll`…) and `code-graph query caller-count` surface shared references — mutable fixtures, module-level state, singletons, shared live services or databases — that more than one test touches. `code-graph query callers --param depth=3` and `importers` confirm whether two tests actually reach the same mutable state or just happen to import the same module; a shared read-only constant is not coupling, a shared mutable object or live external resource is.

Weight live/shared hosted state especially heavily — a test suite pointing at one shared staging environment or database is a strong dependency-risk signal even without explicit code-level coupling.

Classify each real finding by coupling shape (shared fixture, order-dependence, shared live resource) and risk level, then propose an isolation fix — usually scoping the fixture, injecting fresh state per test, or removing an implicit ordering assumption — as a diff in the target project's `proposals/`.

## Reference Example

A real finding from this project's own CRM Playwright suite (captured before Ray's memory architecture was retired — preserved here as a worked example):

All 5 `tests/e2e/crm-workspace/*.spec.ts` files (contacts, deals, notifications, pipeline, settings) shared one live, unreset external app instance via the `crmPage` fixture in `crm.fixtures.ts`. `settings.spec.ts`'s delete-all test wiped every deal/contact on that shared instance; `deals.spec.ts` and `contacts.spec.ts` assumed data already existed. Safety depended entirely on two unenforced accidents: `workers=1`/`fullyParallel=false` in config, and alphabetical file-run order. Evidence: a search for `crm-workspace` showing the `crmPage` fixture's in-degree; a callers query confirming all 5 spec files consumed the same fixture instance; direct reads of the delete-all test and the config flags. Resulting diff proposals (documentation-only hazard warnings, since no reset/seed API existed) live at `proposals/tests__e2e__crm-workspace__settings.spec.ts.1.patch` and `proposals/playwright.config.ts.1.patch` in that project.
