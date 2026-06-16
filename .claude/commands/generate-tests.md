# Generate Tests Command

You are adding test coverage for a feature or module in InvestingBuddy.

---

## Pre-Test Checklist

1. Identify the module to test (service, route, workflow, integration)
2. Inspect the implementation code
3. Identify: happy paths, error cases, permission checks, edge cases
4. Inspect existing tests for patterns to follow

---

## What to Test

### Service Layer (Backend Unit Tests)
- Core business logic paths
- Data transformation and validation
- Error conditions (not found, invalid input, constraint violations)
- Investment domain rules (rating validation, citation requirements)

### API Routes (Integration Tests)
- Happy path response (correct status code and body)
- Invalid input rejection (422 Unprocessable Entity)
- Auth rejection for protected routes (401 / 403)
- User data scoping (user cannot access other users' data)
- Admin endpoint rejects non-admin user

### Database Interactions
- Record creation and retrieval
- Foreign key constraint behavior
- Status field transitions

### Agent Workflows (Smoke Tests)
- Workflow can be instantiated and executed
- Uses mocked LLM responses — never real LLM calls in tests
- `agent_run` record is created
- `agent_step` records are created for each node
- Output structure matches expected schema
- Error handling: workflow handles LLM failure gracefully

### Security Tests
- Admin endpoints reject unauthenticated requests
- Admin endpoints reject authenticated non-admin users
- User endpoints reject requests for other users' data
- Public endpoints do not expose private fields

---

## Mocking Rules

Always mock:
- Azure OpenAI / LLM calls (use `pytest-mock` or `unittest.mock`)
- Azure Blob Storage operations
- Azure AI Search calls
- OpenBB and external financial data APIs
- Email sending

Never use:
- Real Azure credentials in tests
- Real production financial data
- Real LLM tokens

Use a test database:
- SQLite in-memory for simple unit tests
- Separate PostgreSQL test DB for integration tests (using test fixtures)

---

## Test Structure

```python
# Example service unit test pattern
def test_<feature>_<scenario>():
    # Arrange
    <set up inputs and mocks>

    # Act
    result = service.method(...)

    # Assert
    assert result.<field> == expected
```

```python
# Example API integration test pattern
def test_<endpoint>_<scenario>(client, auth_headers):
    response = client.get("/api/...", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["field"] == expected
```

---

## Output Format

```
## Tests Added

### Module tested
<file or module name>

### Test file(s)
- <path>

### Scenarios covered
- [ ] Happy path: <description>
- [ ] Error case: <description>
- [ ] Auth rejection: <description>
- [ ] Edge case: <description>

### Mocks used
- <what was mocked and why>

### Checks run
- pytest: passed/failed
- Coverage: <optional>

### Scenarios NOT covered (known gaps)
- <scenario> — reason
```
