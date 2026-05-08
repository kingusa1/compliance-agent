# Coding Conventions

**Analysis Date:** 2025-02-10

This document covers both backend (Python/FastAPI) and frontend (Next.js 14 with App Router/TypeScript) coding conventions used in the compliance-agent project.

## Backend Conventions (Python/FastAPI)

### Naming Patterns

**Files:**
- Module files use `snake_case.py`: `app/agent/agent_loop.py`, `app/models.py`, `app/auth.py`
- Route modules end in `_routes.py`: `app/agents_routes.py`, `app/customers_routes.py`, `app/deals_routes.py`
- Test files follow `test_*.py`: `tests/test_agent_tools.py`, `tests/test_agent_chat.py`

**Functions:**
- Use `snake_case` for all function definitions
- Async functions prefixed with underscore when internal: `async def _call_llm_with_tools(...)`
- Public API functions (routes/handlers) have no prefix: `def list_agents(...)`, `async def run_chat(...)`
- Helper functions at module level preceded by `_`: `def _build_system_prompt(...)`, `def _canonicalize_agent(...)`

**Classes:**
- Use `PascalCase` for SQLAlchemy model classes: `Profile`, `Call`, `Script`, `AgentLearning`
- Use `PascalCase` for Pydantic schemas: `BaseModel` subclasses follow domain naming (e.g., `CreateUserRequest`)
- Internal helper classes prefixed with underscore: `class FakeJWKClient`, `class _UUIDOrChar(TypeDecorator)`

**Variables:**
- Use `snake_case` for all variables and parameters
- Type hints on all function parameters and returns
- Optional/nullable types use `Type | None` syntax (Python 3.10+)

### Type Annotations

**All public APIs require explicit type hints:**

```python
# From app/auth.py
def verify_jwt(authorization: str | None = Header(default=None)) -> dict:
    """Verify a Supabase-issued JWT using JWKS. Returns the decoded claims."""

# From app/agents_routes.py
def _canonicalize_agent(name: str | None, aliases: dict[str, str]) -> str | None:
```

**Async functions explicitly typed:**

```python
# From app/agent/agent_loop.py
@LLM_RETRY
async def _call_llm_with_tools(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict],
    timeout: float = 90.0,
) -> dict:
```

**Generator and iterator returns:**

```python
# From app/agent/chat.py — AsyncIterator with tuple yields
async def run_chat(
    messages: list[dict[str, Any]],
    db: Session,
    *,
    model: str = DEFAULT_MODEL,
    client: Any = None,
) -> AsyncIterator[tuple[str, Any]]:
```

### Database & SQLAlchemy

**Models use Column + relationship patterns:**

```python
# From app/models.py — mixed SQLAlchemy dialects (Postgres + SQLite)
from sqlalchemy import Column, ForeignKey, String, DateTime
from sqlalchemy.orm import relationship

class Call(Base):
    __tablename__ = "calls"
    id: Mapped[str] = mapped_column(primary_key=True)
    filename: Mapped[str]
    transcript: Mapped[str | None]
```

**Database abstraction handles Postgres/SQLite compatibility:**
- Custom TypeDecorators for `JSONB` ↔ `TEXT`, `ARRAY` ↔ `TEXT(JSON-encoded)`
- Tests run on SQLite; production on Postgres
- See `app/models.py` for `JSONBCompat`, `TextArrayCompat`, `_UUIDOrChar` patterns

**Session management:**
- FastAPI dependency injection: `db: Session = Depends(get_db)`
- Session obtained from `SessionLocal()` for script/batch contexts
- Explicit `db.commit()` after writes; `db.rollback()` in exception paths

### Error Handling

**HTTPException for API errors:**

```python
# From app/auth.py
if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(status_code=401, detail="Missing Bearer token")

try:
    payload = jwt.decode(...)
except ExpiredSignatureError:
    raise HTTPException(status_code=401, detail="Token expired")
except InvalidTokenError as e:
    raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
```

**Graceful degradation for missing columns (backward compatibility):**

```python
# From app/agents_routes.py — handles pre-migration DBs
try:
    rows = db.query(SalesAgentAlias).all()
except Exception:  # OperationalError for missing table
    return {}  # Return empty dict, don't crash
```

**Explicit exception handling (never bare except):**
- Catch specific exceptions: `InvalidTokenError`, `OperationalError`, `ProgrammingError`
- Use `except Exception as e:` only for broad best-effort cleanup paths

### Logging

**Framework:** Standard `logging` module

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Agent loop started", extra={"agent_id": agent_id})
logger.error("Failed to call LLM", exc_info=True)
```

**Custom structured logger (`app.logger`):**
- `from app.logger import log` for context-aware logging
- Used in observability paths (Phase W2)

### Imports Organization

**Order (implicit from codebase patterns):**

1. `__future__` annotations: `from __future__ import annotations`
2. Standard library: `import os`, `import logging`, `from typing import ...`
3. Third-party: `from fastapi import`, `from sqlalchemy import`
4. Local app: `from app.config import settings`, `from app.models import Call`

**Path conventions:**
- Absolute imports: `from app.auth import verify_jwt` (never relative `from .auth`)
- Deep imports allowed for specificity: `from app.agent.tool_handlers import ToolContext`

### Pydantic Usage (v2)

**Pydantic v2 is required:** `pydantic>=2.10,<3` (see `backend/requirements.txt`)

```python
from pydantic import BaseModel, Field

class CreateUserRequest(BaseModel):
    name: str
    email: str
    age: int | None = None
```

**No dataclass usage for API models** — Pydantic BaseModel is the standard for request/response schemas.

### Comments & Docstrings

**Module docstrings required for non-trivial files:**

```python
# From app/auth.py
"""Supabase Auth JWT verification.

Deviates from plan: uses JWKS asymmetric verification (ECC P-256) instead of
HS256 shared-secret. Our Supabase project's current signing key is asymmetric;
PyJWKClient fetches and caches the JWKS automatically and handles rotation.
"""
```

**Function docstrings for public APIs:**

```python
def verify_jwt(authorization: str | None = Header(default=None)) -> dict:
    """Verify a Supabase-issued JWT using JWKS. Returns the decoded claims."""

def _get_jwks_client() -> PyJWKClient:
    """Cache a single JWKS client — it internally caches keys and refreshes on kid miss."""
```

**Inline comments for non-obvious logic:**

```python
# Dev convenience: when DEV_ALL_ADMIN=true, every authenticated user is
# treated as admin regardless of their stored role.
role = "admin" if settings.dev_all_admin else profile.role
```

### Configuration

**Environment via Pydantic Settings:**
- `from app.config import settings`
- `settings.dev_all_admin`, `settings.openrouter_api_key`, `settings.supabase_url`

**No hardcoded values:**
- All API keys, URLs, database credentials via environment
- Validation at startup: routes that require config should fail fast if env vars missing

---

## Frontend Conventions (Next.js 14/TypeScript)

### Next.js App Router Notes

**⚠️ CRITICAL:** This is NOT standard Next.js. See `frontend-v3/AGENTS.md` — APIs, conventions, and file structure may differ from training data. Always consult `node_modules/next/dist/docs/` before writing code.

### Naming Patterns

**Files:**
- Page components: `page.tsx` inside route segments: `src/app/(admin)/agents/page.tsx`
- Layout components: `layout.tsx` at directory roots
- Component files: `PascalCase.tsx` inside `src/components/` or co-located with pages
- Utilities/helpers: `camelCase.ts` in `src/lib/`
- Hooks: `useXxx.ts` in `src/lib/hooks/`

**Components (functional, with TypeScript):**
- Use `PascalCase` for all component names: `AgentsTable`, `CategoryChip`, `CheckpointCard`
- Props interfaces: `{ComponentName}Props`: `interface AgentsTableProps { agents: ... }`

**Functions/Utilities:**
- Use `camelCase` for exported functions: `buildEmailPreview`, `suggestAggregate`
- Use `camelCase` for custom hooks: `useObservabilityStream`, `useUrlState`, `useDebouncedValue`

**Variables/Constants:**
- Use `camelCase` for mutable state, props
- Use `UPPER_SNAKE_CASE` for constants: `REJECTION_CATEGORIES`, `REJECTION_CATEGORY_COLORS`

### Type Annotations

**All component props must be typed via interface or type:**

```typescript
// From frontend-v3/src/app/(admin)/agents/AgentsTable.tsx
export type AgentsTableProps = {
  agents: AgentLeaderboardRow[];
};

export function AgentsTable({ agents }: AgentsTableProps) {
  // ...
}
```

**Avoid `any` — use `unknown` and narrow safely:**

```typescript
// Type narrowing pattern used in error handling
function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error";
}
```

**Infer types from runtime values:**

```typescript
// Extract form props from schema
const submitSchema = z.object({...});
type SubmitRequest = z.infer<typeof submitSchema>;
```

### Server vs. Client Components

**By default, all components are Server Components** unless marked `"use client"`:

```typescript
// Server Component (default) — can access backend secrets, DB
export default function CallsList({ calls }: { calls: Call[] }) {
  // Can call DB directly
  const results = await db.query(...);
  return <div>{results.map(...)}</div>;
}

// Client Component — requires "use client" directive
"use client";
export function InteractiveButton() {
  const [count, setCount] = useState(0);
  return <button onClick={() => setCount(count + 1)}>{count}</button>;
}
```

**Route organization with grouping:**
- `(admin)` — routes behind admin role check
- `(reviewer)` — routes for reviewer/lead users
- Ungrouped routes in root are public/shared

### React Hooks & State Management

**TanStack Query (React Query) for server state:**

```typescript
// Fetch pattern — useQuery hook from TanStack Query
const { data, isLoading, error } = useQuery({
  queryKey: ["agents"],
  queryFn: async () => {
    const res = await fetch("/api/agents");
    return res.json();
  },
});
```

**useState for UI state only (not server data):**

```typescript
const [isOpen, setIsOpen] = useState(false);
const [selectedId, setSelectedId] = useState<string | null>(null);
```

**Custom hooks in `src/lib/hooks/`:**

```typescript
// From useUrlState.ts — encapsulates URL param sync
export function useUrlState(param: string): [value: string, setValue: (v: string) => void]
```

**useEffect for side effects (narrow scope):**

```typescript
useEffect(() => {
  router.replace("/tracker");
}, [router]);
```

### Component Composition & UI Framework

**shadcn/ui + Radix UI are the primary component libraries:**
- Import from `@/components/ui/`: `Button`, `Table`, `Dialog`, `Badge`
- Components are generated via `npx shadcn@latest add`
- Styling via Tailwind CSS classes (dark mode enabled by default)

**Example from AgentsTable:**

```typescript
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

export function AgentsTable({ agents }: AgentsTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Agent</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {agents.map(a => <TableRow key={a.agent_name}>...</TableRow>)}
      </TableBody>
    </Table>
  );
}
```

### Styling Conventions

**Tailwind CSS with custom CSS variables:**
- Dark mode enabled by default: `dark` class on `<html>`
- Color palette from globals.css: `--bg-canvas`, `--bg-elev1`, `--border-subtle`, `--text-primary`, `--text-muted`
- Use CSS custom properties in classes: `bg-[var(--bg-elev1)]`, `text-[var(--text-primary)]`

**Class patterns:**
- Use arbitrary Tailwind values for custom colors: `bg-[var(--bg-elev2)]`
- Use Tailwind utilities for spacing, sizing, typography: `overflow-hidden`, `rounded-lg`, `text-[13px]`

### Data Fetching Patterns

**No fetch in components — use server actions or API routes:**

```typescript
// ❌ WRONG: fetch in component
function MyComponent() {
  useEffect(() => {
    fetch("/api/data").then(...);
  }, []);
}

// ✓ CORRECT: fetch in server action or API route, query via useQuery
const { data } = useQuery({
  queryKey: ["data"],
  queryFn: async () => {
    const res = await fetch("/api/data");
    return res.json();
  },
});
```

**Supabase client at `src/lib/supabase.ts`:**
- Creates anon-key client for browser-safe operations
- Handles auth state persistence
- Gracefully handles stale token refresh loops (see implementation comments)

### Error Boundaries & Error Handling

**Try-catch for async operations:**

```typescript
async function loadData() {
  try {
    const result = await riskyOperation();
    return result;
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    toast.error(message);
    throw new Error(message);
  }
}
```

**Toast notifications for user-facing errors:**
- `toast.error()`, `toast.success()` from `sonner` component
- Import: `import { Toaster } from "@/components/ui/sonner"`

### Immutability

**Spread operator for state updates:**

```typescript
// ❌ WRONG: mutation
call.status = "completed";
setCall(call);

// ✓ CORRECT: immutability
setCall({ ...call, status: "completed" });
```

**Array immutability patterns:**

```typescript
// Map to new array
const updated = items.map(item =>
  item.id === target.id ? { ...item, status: "done" } : item
);
setItems(updated);

// Filter to remove
setItems(items.filter(item => item.id !== targetId));
```

### Comments & Documentation

**JSDoc for exported functions (when useful):**

```typescript
/**
 * CategoryChip — Watt's hand-typed 5-color category palette is load-bearing.
 * If any of these constants drift, the /rejections page silently mis-codes
 * the category and Watt's reviewers stop trusting the chip.
 */
```

**Inline comments for non-obvious logic:**

```typescript
// Successful auth lands on /queue (reviewer/lead) or /calls (admin/user).
await page.waitForURL(/\/(queue|calls|customers|deals)/, { timeout: 15_000 });
```

### Configuration

**Environment via `.env.local`:**
- `NEXT_PUBLIC_SUPABASE_URL` — Supabase API endpoint
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — Supabase anon/public key
- `BASE_URL` — dev server base (default `:3005`)

**No hardcoded secrets in source:**
- All API keys, URLs via environment
- Public-facing keys prefixed `NEXT_PUBLIC_`

---

## Shared Patterns

### Dependency Injection

**Backend (FastAPI):**

```python
@app.get("/api/resource")
def get_resource(db: Session = Depends(get_db)) -> dict:
    return {"result": db.query(...).all()}
```

**Frontend (React Context + custom hooks):**

```typescript
const { data } = useQuery({
  queryKey: ["key"],
  queryFn: async () => await fetch(...),
});
```

### API Response Format

**Backend response envelope (implicit pattern):**

```python
# Routes return dict or JSONResponse
return {
    "status": "success",
    "data": [...],
    "meta": {"total": 100, "page": 1}
}
```

### Testing Conventions

See TESTING.md for comprehensive test patterns.

---

*Convention analysis: 2025-02-10*
