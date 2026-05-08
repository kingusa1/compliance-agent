# Testing Patterns

**Analysis Date:** 2025-02-10

This document covers testing strategy, frameworks, and patterns for both backend (Python/pytest) and frontend (TypeScript/Vitest + Playwright) in the compliance-agent project.

## Backend Testing (Python/pytest)

### Test Framework

**Runner:**
- `pytest` 8.3.0 (see `backend/requirements.txt`)
- Config: `backend/pytest.ini`
- Async support: `pytest-asyncio` 0.24.0

**Coverage:**
- `pytest-cov` 5.0.0
- No minimum enforced, but aim for 80%+ on critical paths

**Run Commands:**

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agent_tools.py

# Watch mode (requires pytest-watch)
pytest-watch tests/

# With coverage report
pytest --cov=app --cov-report=term-missing

# Run only tests marked with 'unit'
pytest -m unit

# Run only tests marked with 'integration'
pytest -m integration
```

### Test File Organization

**Location:**
- All tests in `backend/tests/` directory
- Mirrors application structure where applicable: `tests/test_agent_*.py` for `app/agent/*`
- Naming: `test_<module>.py` (e.g., `test_agent_tools.py`, `test_agent_chat.py`)

**Root test utilities:**
- `tests/conftest.py` — shared fixtures (database, auth, mocking)

### Pytest Configuration

**From `backend/pytest.ini`:**

```ini
[pytest]
testpaths = tests
asyncio_default_fixture_loop_scope = function
filterwarnings =
    ignore::DeprecationWarning:pydantic.*
    ignore::DeprecationWarning:supabase.*
    ignore::DeprecationWarning:deepgram.*
    ignore::DeprecationWarning:websockets.*
    ignore::DeprecationWarning:PyPDF2.*
```

### Test Structure

**Unit test pattern (pure functions, no DB):**

```python
# From tests/test_agent_tools.py
TRANSCRIPT = (
    "[00:05] Agent: Hi, is it Adam speaking? "
    "[00:07] Customer: Yeah, speaking. "
    # ...
)

WORD_DATA = [
    {"word": "Hi", "speaker": "A", "start": 5.0, "end": 5.2, "confidence": 0.95},
    # ...
]

def _ctx(db=None):
    """Helper to build ToolContext for testing."""
    return ToolContext(
        transcript=TRANSCRIPT,
        word_data=WORD_DATA,
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=db,
    )

def test_find_evidence_high_similarity():
    result = find_evidence(_ctx(), query="standing charge 30 pence per day")
    assert result["verified"] is True
    assert result["similarity"] >= 0.75
    assert "30 pence per day" in result["best_match"]
```

**Key patterns:**
- Setup test data at module level (constants like `TRANSCRIPT`, `WORD_DATA`)
- Helper factories (`_ctx()`) to build context objects
- One assertion per test or logical group (AAA pattern: Arrange, Act, Assert)

### Fixtures

**Core fixtures from `tests/conftest.py`:**

#### `test_db` — Temporary SQLite database

```python
@pytest.fixture
def test_db():
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    os.unlink(path)
```

**Usage:**
```python
def test_get_similar_learnings_empty_db(test_db):
    r = get_similar_learnings(
        _ctx(db=test_db),
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
    )
    assert r["found"] == 0
```

#### `auth()` — JWT header helper

```python
@pytest.fixture
def auth():
    """Return a callable that builds Authorization header for a reviewer id."""
    def _auth(reviewer_id: str) -> dict:
        return {"Authorization": f"Bearer {_make_jwt(reviewer_id)}"}
    return _auth
```

**Usage:**
```python
def test_protected_route(client, auth):
    r = client.post("/api/endpoint", headers=auth("sarah"))
    assert r.status_code == 200
```

#### `mock_jwks` — JWT verification mock

```python
@pytest.fixture
def mock_jwks(monkeypatch):
    """Replace PyJWKClient so tests verify with test ES256 key."""
    # Creates fake JWKS client that uses test public key
    # Allows all auth-dependent tests to work without real Supabase
```

**Used by:** Any test requiring JWT verification (route tests with auth headers)

#### `seed_profiles` — Test user data

```python
@pytest.fixture
def seed_profiles(test_db):
    """Seed 4 test profiles (3 reviewers + 1 lead)."""
    from app.models import Profile
    
    test_db.add_all([
        Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
        Profile(id="mo",    email="mo@test.local",    name="Mo Ibrahim",  role="reviewer", active=True),
        Profile(id="layla", email="layla@test.local", name="Layla Said",  role="reviewer", active=True),
        Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
    ])
    test_db.commit()
```

**Used by:** Tests that check role-based authorization (lead vs. reviewer)

#### `no_dev_admin` — Override dev convenience flag

```python
@pytest.fixture
def no_dev_admin(monkeypatch):
    """Disable DEV_ALL_ADMIN flag so role-gated tests see stored roles.
    
    Wave 4 added DEV_ALL_ADMIN that makes every user an admin in dev.
    Tests that verify 403/forbidden need this disabled.
    """
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield
```

**Used by:** Tests checking authorization failures (403 Forbidden)

#### `_reset_dependency_overrides_after_test` — Cleanup isolation

```python
@pytest.fixture(autouse=True)
def _reset_dependency_overrides_after_test():
    """Clean up FastAPI dependency overrides after each test.
    
    Many tests override app.dependency_overrides[get_db] but never tear down.
    This runs automatically after every test to restore the original state.
    """
    from app.main import app as _app
    snapshot = dict(_app.dependency_overrides)
    yield
    _app.dependency_overrides.clear()
    _app.dependency_overrides.update(snapshot)
```

**Automatically used:** No need to explicitly use this fixture — runs after every test

#### `db_session_with_call_with_transcript` — Seeded call data

```python
@pytest.fixture
def db_session_with_call_with_transcript() -> str:
    """Seed a Call row with transcript/word_data/script_id so the replay 
    endpoint accepts it. Yields call_id; cleans up on teardown."""
```

**Used by:** Tests that need realistic Call objects with full metadata

#### `upload_dir` — Temporary file upload directory

```python
@pytest.fixture
def upload_dir(tmp_path):
    """Temporary upload directory."""
    d = tmp_path / "uploads"
    d.mkdir()
    return str(d)
```

**Used by:** Tests that verify file upload handling

### Mocking Strategy

**Mock external services, not domain logic:**

```python
# ✓ CORRECT: Mock the LLM call, test tool logic
def test_agent_loop_calls_llm(monkeypatch):
    mock_llm_response = {
        "choices": [{"message": {...}}]
    }
    monkeypatch.setattr(
        "app.agent.agent_loop._call_llm_with_tools",
        Mock(return_value=mock_llm_response)
    )
    # Test that batch processing calls LLM correctly

# ✗ WRONG: Don't mock domain logic — test real behavior
def test_find_evidence(monkeypatch):
    monkeypatch.setattr(find_evidence, ...)  # BAD: mock the thing being tested
```

**Monkeypatch for dependency injection:**

```python
def test_with_custom_db(monkeypatch, custom_db_session):
    monkeypatch.setattr("app.database.get_db", lambda: custom_db_session)
    # Route now uses custom_db_session instead of real connection
```

### Test Categories

**Mark tests for selective execution:**

```python
import pytest

@pytest.mark.unit
def test_pure_function():
    pass

@pytest.mark.integration
def test_database_operation():
    pass

@pytest.mark.slow
def test_llm_call_timeout():
    pass
```

**Run by category:**
```bash
pytest -m unit        # Only unit tests
pytest -m integration # Only integration tests
pytest -m "not slow"  # Skip slow tests
```

### Coverage Target

**Aim for 80%+ on critical paths:**
- Agent logic (tool execution, tool calling)
- Database models and queries
- Auth and permission checks
- Error handling paths

**Lower priority for:**
- Configuration parsing
- External API mocking (just verify call shape)
- Logging

### Testing Async Code

**pytest-asyncio handles async fixtures and tests:**

```python
import pytest

@pytest.fixture
async def async_client():
    async with httpx.AsyncClient() as client:
        yield client

@pytest.mark.asyncio
async def test_async_operation(async_client):
    response = await async_client.post("/api/endpoint")
    assert response.status_code == 200
```

---

## Frontend Testing (TypeScript/Vitest + Playwright)

### Test Frameworks

**Unit Tests:**
- `vitest` 4.1.5 — Jest-compatible test runner for components
- `@testing-library/react` — Component rendering and interaction
- `@testing-library/jest-dom` — Assertion helpers for DOM

**E2E Tests:**
- `@playwright/test` 1.59.1 — Headless browser automation
- `@axe-core/playwright` 4.11.3 — Accessibility testing

**Config:**
- `frontend-v3/vitest.config.ts` — Unit test setup
- `frontend-v3/playwright.config.ts` — E2E test setup

### Run Commands

```bash
# Unit tests (vitest)
npm run test              # Run all unit tests
npm run test:unit        # Run all unit tests (alias)
npm run test:watch       # Watch mode (re-run on file change)

# E2E tests (Playwright)
npm run test:e2e         # Run Playwright tests
npm run e2e              # Same as test:e2e

# View E2E results
npx playwright show-report  # Open HTML report after test run
```

### Unit Test Setup

**From `frontend-v3/vitest.config.ts`:**

```typescript
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
    css: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

**From `frontend-v3/tests/setup.ts`:**

```typescript
import "@testing-library/jest-dom/vitest";

// Stub Supabase env so createClient() doesn't throw at import time
process.env.NEXT_PUBLIC_SUPABASE_URL ??= "http://stub.invalid";
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??= "stub-anon-key";
```

**Key setup details:**
- `jsdom` environment so React Testing Library can mount components
- `globals: true` — no need to import `describe`, `it`, `expect`
- `setupFiles` wire up Testing Library assertions
- Environment variables stubbed for Supabase client
- Network calls are mocked per-test (no real API calls)

### Unit Test File Organization

**Location:**
- Tests in `frontend-v3/tests/unit/` mirror component structure
- Naming: `{ComponentName}.test.tsx` or `{moduleName}.test.ts`

**Example files:**
- `tests/unit/CategoryChip.test.tsx` — tests for `src/app/(admin)/rejections/CategoryChip`
- `tests/unit/email-preview.test.tsx` — tests for email preview builder
- `tests/unit/CheckpointEditor.test.tsx` — tests for checkpoint editor component

### Unit Test Structure

**Component test example (from `tests/unit/CategoryChip.test.tsx`):**

```typescript
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { CategoryChip, CategoryBadgeLarge } from "@/app/(admin)/rejections/CategoryChip";
import {
  REJECTION_CATEGORIES,
  REJECTION_CATEGORY_COLORS,
  REJECTION_CATEGORY_LABELS,
} from "@/lib/schemas/rejections";

/**
 * CategoryChip — Watt's hand-typed 5-color category palette is load-bearing.
 * If any of these constants drift, the /rejections page silently mis-codes
 * the category and Watt's reviewers stop trusting the chip.
 */
describe("CategoryChip", () => {
  it("ships exactly 8 categories with W2-locked hex colors", () => {
    expect(REJECTION_CATEGORIES).toHaveLength(8);
    expect(REJECTION_CATEGORY_COLORS.ADMIN_ERROR).toBe("#FFC000");
    expect(REJECTION_CATEGORY_COLORS.PROCESS_FAILURE).toBe("#00B0F0");
  });

  it("renders the small chip with category data attribute + label", () => {
    const { getByText, container } = render(
      <CategoryChip category="ADMIN_ERROR" />,
    );
    expect(getByText(REJECTION_CATEGORY_LABELS.ADMIN_ERROR)).toBeTruthy();
    const chip = container.querySelector("[data-slot=category-chip]");
    expect(chip).not.toBeNull();
    expect(chip?.getAttribute("data-category")).toBe("ADMIN_ERROR");
  });

  it("renders the large badge for the detail panel header", () => {
    const { container } = render(<CategoryBadgeLarge category="VERBAL_SALES_ERROR" />);
    const badge = container.querySelector("[data-slot=category-badge-large]");
    expect((badge as HTMLElement).style.background).toBe("rgb(255, 0, 0)");
  });

  it("falls back to a mono label when given an unknown category", () => {
    const { getByText } = render(<CategoryChip category="UNKNOWN_VALUE" />);
    expect(getByText("UNKNOWN_VALUE")).toBeTruthy();
  });
});
```

**Key patterns:**
- Use `describe()` blocks to group related tests
- Use `it()` for individual test cases
- `render()` to mount component with React Testing Library
- Query DOM via `getByText()`, `container.querySelector()`, etc.
- Assert properties and behavior, not implementation

**Pure function test example (from `tests/unit/email-preview.test.tsx`):**

```typescript
describe("suggestAggregate", () => {
  it("returns null when no actions are set", () => {
    expect(suggestAggregate(new Map())).toBeNull();
  });

  it("returns PASS when every action is no_action / ignore", () => {
    const m = new Map<string, PerCpAction>([
      ["a", "no_action"],
      ["b", "ignore"],
    ]);
    expect(suggestAggregate(m)).toBe("PASS");
  });

  it("recall_redo wins over coach + reask_supp → FAIL", () => {
    const m = new Map<string, PerCpAction>([
      ["a", "coach"],
      ["b", "reask_supp"],
      ["c", "recall_redo"],
    ]);
    expect(suggestAggregate(m)).toBe("FAIL");
  });
});
```

### E2E Test Setup

**From `frontend-v3/playwright.config.ts`:**

```typescript
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3005",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    port: 3005,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
```

**Key config details:**
- `testDir: "./tests/e2e"` — E2E tests in separate directory
- `webServer` — Auto-starts dev server on `:3005` (or reuses if running)
- `reuseExistingServer` — True locally (faster), false in CI (clean slate)
- Single `workers: 1` — No parallelism (shared login fixture)
- Screenshots and traces captured on failure
- 2 retries in CI for flake tolerance

### E2E Test File Organization

**Location:**
- `frontend-v3/tests/e2e/*.spec.ts`
- Named by feature: `reviewer-happy-path.spec.ts`, `admin-upload.spec.ts`, `a11y-audit.spec.ts`

**Example files:**
- `tests/e2e/reviewer-happy-path.spec.ts` — Full reviewer workflow
- `tests/e2e/admin-upload.spec.ts` — Admin file upload and processing
- `tests/e2e/a11y-audit.spec.ts` — Accessibility checks with axe

### E2E Fixtures

**From `frontend-v3/tests/e2e/fixtures.ts`:**

```typescript
import { test as base, expect, type Page } from "@playwright/test";

/**
 * Shared E2E fixtures (W5).
 *
 * `authedPage` logs in via the regular Supabase form once per worker —
 * faster + more reliable than juggling tokens, and exercises the real
 * /login page along the way.
 */
const E2E_EMAIL = process.env.E2E_EMAIL ?? "test@fame.dev";
const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "test";

type Fixtures = {
  authedPage: Page;
};

export const test = base.extend<Fixtures>({
  authedPage: async ({ page }, use) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', E2E_EMAIL);
    await page.fill('input[type="password"]', E2E_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    // Successful auth lands on /queue (reviewer/lead) or /calls (admin/user).
    await page.waitForURL(/\/(queue|calls|customers|deals)/, { timeout: 15_000 });
    await use(page);
  },
});

export { expect };
```

**Usage in tests:**

```typescript
import { test, expect } from "../fixtures";

test("reviewer completes a compliance review", async ({ authedPage }) => {
  // authedPage is pre-authenticated and ready to use
  await authedPage.goto("/queue");
  expect(await authedPage.locator("h1").textContent()).toContain("Review Queue");
});
```

**Key pattern:**
- Single auth per worker (not per-test) — faster than logging in N times
- Credentials from env vars: `E2E_EMAIL`, `E2E_PASSWORD`
- Waits for navigation to confirm auth success
- Page is already authenticated when test starts

### E2E Test Structure

**Basic pattern:**

```typescript
import { test, expect } from "../fixtures";

test.describe("Feature: Customer Workflow", () => {
  test("customer can view their recent deals", async ({ authedPage }) => {
    await authedPage.goto("/customers/john-smith");
    
    const heading = authedPage.locator("h1");
    await expect(heading).toContainText("John Smith");
    
    const dealRows = authedPage.locator("[data-testid=deal-row]");
    const count = await dealRows.count();
    expect(count).toBeGreaterThan(0);
  });

  test("customer can filter deals by status", async ({ authedPage }) => {
    await authedPage.goto("/customers/john-smith");
    
    const filterBtn = authedPage.getByRole("button", { name: /filter/i });
    await filterBtn.click();
    
    const compliantCheckbox = authedPage.locator("[name=status-compliant]");
    await compliantCheckbox.check();
    
    const results = authedPage.locator("[data-testid=filtered-deal]");
    const count = await results.count();
    expect(count).toBeGreaterThan(0);
  });
});
```

**Key patterns:**
- `test.describe()` groups related tests
- `authedPage.goto()` navigates to route
- `locator()` finds elements (CSS selector, text, role-based)
- `expect()` asserts visibility, content, count
- Tests are sequential (one after another, not parallel)

### Accessibility Testing

**From `tests/e2e/a11y-audit.spec.ts`:**

```typescript
import { injectAxe, checkA11y } from "axe-playwright";

test("homepage is accessible (WCAG 2.0 AA)", async ({ page }) => {
  await page.goto("/");
  await injectAxe(page);
  await checkA11y(page, null, {
    detailedReport: true,
    detailedReportOptions: { html: true },
  });
});
```

**Runs automatic accessibility checks:**
- WCAG 2.0 AA compliance
- Keyboard navigation
- Screen reader compatibility
- Color contrast

### Testing Async/Network Operations

**Mock fetch/HTTP calls in unit tests:**

```typescript
import { vi } from "vitest";

test("loads data and displays it", async () => {
  global.fetch = vi.fn().mockResolvedValueOnce({
    ok: true,
    json: async () => ({ id: 1, name: "Test" }),
  });

  const { getByText } = render(<MyComponent />);
  
  await waitFor(() => {
    expect(getByText("Test")).toBeInTheDocument();
  });
});
```

**For E2E, let real API calls happen:**
- Tests hit actual backend
- Backend is running (either dev server or test server)
- Network delays are part of the test

### Testing Error States

**Component test for error boundary:**

```typescript
test("displays error when data fetch fails", async () => {
  global.fetch = vi.fn().mockRejectedValueOnce(new Error("Network error"));

  const { getByText } = render(<MyComponent />);
  
  await waitFor(() => {
    expect(getByText(/error|failed/i)).toBeInTheDocument();
  });
});
```

**E2E test for error path:**

```typescript
test("shows error message when upload fails", async ({ authedPage }) => {
  await authedPage.goto("/upload");
  
  const uploadBtn = authedPage.getByRole("button", { name: /upload/i });
  // Server rejects request
  
  const errorMsg = authedPage.locator("[role=alert]");
  await expect(errorMsg).toContainText("Upload failed");
});
```

### Testing Query Parameters & URL State

**useUrlState hook pattern:**

```typescript
test("persists selected category in URL", () => {
  const { getByRole } = render(<FilterPanel />);
  
  const categoryBtn = getByRole("button", { name: "Verbal Sales Error" });
  fireEvent.click(categoryBtn);
  
  // URL should change to ?category=verbal_sales_error
  expect(window.location.search).toContain("category=verbal_sales_error");
});
```

---

## Test Coverage Goals

**Critical paths requiring 80%+ coverage:**
- Agent tool execution (`app/agent/tool_handlers.py`)
- Authorization checks (`app/auth.py`)
- Database models and queries
- Component rendering (unit tests)
- Happy-path user workflows (E2E tests)

**Lower priority:**
- Configuration parsing
- Third-party API mocking shape verification
- Logging output validation

---

*Testing analysis: 2025-02-10*
