### Title
User enumeration via distinguishable error messages/timing in login (`CreateSession`) - ([File: core/sessions/localauth/orm.go])

### Summary
`orm.CreateSession` returns a raw, unwrapped database error when `FindUser` fails to locate the given email, but returns a hand-crafted `"Invalid email"` or `"Invalid password"` message once a user row is found. Because `SessionsController.Create` forwards the raw error text verbatim to the client and only reaches the expensive `bcrypt` password check when a user exists, an unauthenticated caller can distinguish valid emails from invalid ones by response content and by timing.

### Finding Description
`CreateSession` first calls `o.FindUser(ctx, sr.Email)` [1](#0-0) . For a nonexistent email, `findUser` propagates the raw SQL "no rows" error directly, unlike other ORM methods in the file (e.g. `findValidSession`, `TestPassword`, `UpdateRole`) which wrap lookup failures into a generic message [2](#0-1) . If the user is found, execution proceeds to `constantTimeEmailCompare` and, on match, to `utils.CheckPasswordHash` (bcrypt) before returning `"Invalid password"` on mismatch [3](#0-2) .

`SessionsController.Create` passes whichever error is returned straight into `jsonAPIError(c, http.StatusUnauthorized, err)` without normalizing the message [4](#0-3) . This means:
- Nonexistent email → HTTP 401 with the raw DB error text (e.g. `sql: no rows in result set`).
- Existing email + wrong password → HTTP 401 with `"Invalid password"`.

Additionally, only the existing-email path performs a bcrypt comparison (`CheckPasswordHash`), which is computationally expensive and adds measurable, repeatable latency relative to the immediate short-circuit on a DB miss. Both response-content and timing differences allow email enumeration.

### Impact Explanation
This is a user enumeration side channel that undermines authentication soundness for the node's admin/API login endpoint (`/sessions`). It does not by itself grant authentication bypass, key disclosure, or fund movement, but it allows an unauthenticated attacker to build a list of valid administrator emails on a Chainlink node, which can be used to focus follow-on credential-stuffing/brute-force/phishing attacks against real accounts. This maps to a low/informational-severity authentication-hygiene issue rather than a direct compromise.

### Likelihood Explanation
No privileges are required — any unauthenticated client of `POST /sessions` can trigger both code paths trivially and repeatably by varying the email in the request body. The distinguishing signal (differing error text, and bcrypt-induced latency difference) is deterministic and does not depend on race conditions or environment specifics.

### Recommendation
- In `findUser`/`FindUser`, wrap "no rows" into the same generic error (or handle it explicitly in `CreateSession`) so both "email not found" and "wrong password" return an identical string (e.g., `"Invalid email or password"`).
- Perform a dummy/constant-cost bcrypt comparison (against a static hash) when the user is not found, so response timing is equalized between the "unknown email" and "wrong password" cases.
- Ensure `SessionsController.Create` never forwards raw internal/DB error text to the client for the login endpoint.

### Proof of Concept
Go table-driven unit test in `core/sessions/localauth/orm_test.go`:
1. Seed one known user with a valid password.
2. Case A: call `orm.CreateSession` with a nonexistent email + arbitrary password; capture `err.Error()` and elapsed time.
3. Case B: call `orm.CreateSession` with the known user's email + wrong password; capture `err.Error()` and elapsed time.
4. Assert `err.Error()` strings are identical between Case A and Case B (currently they differ: raw SQL error vs `"Invalid password"`).
5. Assert elapsed time for Case A and Case B are within a small tolerance (currently Case A returns much faster since it short-circuits before `CheckPasswordHash`).
6. Handler-level integration test hitting `POST /sessions` via `SessionsController.Create` asserting identical HTTP status and identical JSON error body for both cases.

### Citations

**File:** core/sessions/localauth/orm.go (L55-59)
```go
func (o *orm) findUser(ctx context.Context, email string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE lower(email) = lower($1)"
	err = o.ds.GetContext(ctx, &user, sql, email)
	return
}
```

**File:** core/sessions/localauth/orm.go (L144-148)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/localauth/orm.go (L154-162)
```go
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```
