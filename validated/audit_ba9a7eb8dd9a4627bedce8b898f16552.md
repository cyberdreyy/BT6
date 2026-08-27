### Title
User existence disclosure via timing/error-text asymmetry in `CreateSession` before password check - ([File: core/sessions/localauth/orm.go])

### Summary
`CreateSession` calls `o.FindUser` (a plain DB lookup) before any constant-time comparison, and only invokes the expensive `utils.CheckPasswordHash` (bcrypt) when a user row is actually found. This produces two independently attacker-observable signals — response latency and error content — that let an unauthenticated caller determine whether a given email address has a registered node account.

### Finding Description
In `core/sessions/localauth/orm.go`, `CreateSession` does: [1](#0-0) 
`FindUser` runs `SELECT * FROM users WHERE lower(email) = lower($1)` and returns the raw `sql.ErrNoRows`-derived driver error immediately if no row matches: [2](#0-1) 

Only if a user row exists does execution reach the bcrypt-based `utils.CheckPasswordHash(sr.Password, string(user.HashedPassword))` call, which returns the distinct, human-readable `"Invalid password"` error: [3](#0-2) 

The HTTP handler `SessionsController.Create` forwards whatever error `CreateSession` returns directly into the JSON response body via `jsonAPIError`, which serializes `err.Error()` when the error isn't a `*models.JSONAPIErrors`: [4](#0-3) [5](#0-4) 

This produces two attacker-visible signals for `POST /sessions`:
1. **Error text**: nonexistent email → raw DB "no rows" error text; existing email + wrong password → `"Invalid password"`. These are different strings returned in the JSON body.
2. **Timing**: nonexistent email path returns after a single indexed SQL lookup; existing-email path additionally performs a bcrypt hash comparison (`CheckPasswordHash`), which is deliberately slow (cost factor), adding tens of milliseconds of latency that is trivially distinguishable from network jitter with a handful of repeated requests.

The `constantTimeEmailCompare` at line 154 only guards against timing differences in comparing the *returned* user's email against the submitted email (a self-consistency check after the row is already found) — it does nothing to normalize the FindUser-miss path vs. the FindUser-hit-but-wrong-password path, so it does not close this gap.

### Impact Explanation
This is a user/account enumeration issue: an unauthenticated attacker who only knows or guesses an email address can determine whether that email corresponds to a registered Chainlink node operator/API account, via either the differing error text or the measurable latency difference (bcrypt vs. no bcrypt). This does not grant authentication bypass, credential disclosure, or fund movement by itself — it is reconnaissance information that could be used to target credential-stuffing/phishing/brute-force efforts against confirmed accounts. This maps to a low-severity "sensitive information disclosure" / account enumeration class, not a critical authentication bypass.

### Likelihood Explanation
Fully unauthenticated and trivially repeatable: an attacker sends `POST /sessions` with arbitrary emails and observes response body text and/or timing, no credentials or special role required. Feasibility is high for the error-text signal (deterministic, no statistics needed) and moderate-to-high for the timing signal (requires a handful of samples to average out network noise, standard technique).

### Recommendation
- Always perform a constant-time/constant-cost operation regardless of whether the user exists: e.g., run a dummy bcrypt comparison against a fixed/dummy hash when `FindUser` returns "not found", so total latency is equalized between the two paths.
- Normalize all `CreateSession` failure paths (email not found, wrong password, invalid email) to return the same generic error (e.g., `"invalid email or password"`) and the same HTTP status code, instead of leaking `"Invalid password"` vs. a raw SQL error string.
- Ensure `jsonAPIError`/`SessionsController.Create` never forwards internal DB error text (`sql.ErrNoRows`, driver messages) to the client.

### Proof of Concept
Go table test in `core/sessions/localauth/orm_test.go` extending `TestORM_CreateSession`:
1. Create one real user (`initial`).
2. Case A: call `CreateSession` with `initial.Email` + wrong password; capture `err.Error()` and elapsed time.
3. Case B: call `CreateSession` with a nonexistent email + same wrong password; capture `err.Error()` and elapsed time.
4. Assert (currently failing): `err.Error()` strings from A and B are identical (today they differ: `"Invalid password"` vs. DB-derived not-found error).
5. Repeat each case N times (e.g., 50) and assert average elapsed time for A and B are within a small tolerance (today, case A should be measurably slower due to the bcrypt call in `utils.CheckPasswordHash`).
6. Handler-level integration test extending `TestSessionsController_Create` in `core/web/sessions_controller_test.go`: POST `/sessions` for both cases and assert identical response body content (today the raw DB error vs. `"Invalid password"` differ), demonstrating the enumeration signal reaches the HTTP client.

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

**File:** core/sessions/localauth/orm.go (L159-162)
```go
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

**File:** core/web/helpers.go (L21-29)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```
