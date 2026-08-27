### Title
Login response body and timing distinguish valid vs invalid emails at POST /sessions, enabling user enumeration - ([File: core/web/sessions_controller.go])

### Summary
`SessionsController.Create` and the underlying `CreateSession` ORM implementation return distinguishable error messages ("Invalid email" vs "Invalid password") and exhibit different processing costs (bcrypt hash comparison only occurs when the email is found) for non-existent vs existing accounts. Since `jsonAPIError` echoes `err.Error()` directly into the JSON response body, an unauthenticated attacker hitting the rate-limited `/sessions` endpoint can enumerate valid node user emails.

### Finding Description
The route is registered as intentionally unauthenticated: `unauth.POST("/sessions", sc.Create)` [1](#0-0) . `SessionsController.Create` calls `sc.App.AuthenticationProvider().CreateSession(ctx, sr)` and on any error responds with `jsonAPIError(c, http.StatusUnauthorized, err)` [2](#0-1) . `jsonAPIError` puts the raw `err.Error()` string into the JSON response body when the error isn't a `*models.JSONAPIErrors` [3](#0-2) .

In `orm.CreateSession`, the flow is:
1. `user, err := o.FindUser(ctx, sr.Email)` — for a non-existent email this returns `sql.ErrNoRows`-style DB error nearly immediately, and that raw DB error is propagated up unmodified (not normalized to a generic message) [4](#0-3) .
2. If the user is found, a constant-time email compare is performed, then `utils.CheckPasswordHash(sr.Password, ...)` — a bcrypt comparison, which is computationally expensive (typically tens of milliseconds) — and on mismatch it returns the distinct string `"Invalid password"` [5](#0-4) .

This produces two independent side channels:
- **Response body**: unknown email returns the raw DB error (e.g., `sql: no rows in result set` or similar), whereas a known email with wrong password returns literally `"Invalid password"`. These are trivially distinguishable strings returned in the JSON body via `jsonAPIError`.
- **Timing**: the bcrypt hash check only executes when `FindUser` succeeds, so requests against valid emails take measurably longer than requests against invalid emails, forming a timing oracle independent of the body content.

While the constant-time compare on line 154 protects against timing differences *between* two valid emails, it does nothing to equalize the cost between "found user, went through bcrypt" and "user not found, short-circuited at `FindUser`." The existing `rateLimiter` on `/sessions` (`rl.UnauthenticatedPeriod()`/`rl.Unauthenticated()`) throttles request volume but does not obscure timing or body differences, and does not prevent low-and-slow enumeration.

### Impact Explanation
This directly matches the "AUTHENTICATION_SOUNDNESS" invariant described in the question: an unauthenticated network attacker can distinguish valid vs. invalid node operator email addresses. This reduces the cost of subsequent targeted credential-stuffing or brute-force attacks against confirmed valid accounts, which map to real Chainlink node users with control over jobs, keys, and funds. This is an information disclosure / authentication boundary weakness rather than a direct authentication bypass.

### Likelihood Explanation
- Preconditions: only network access to the public/admin `/sessions` endpoint is required; no credentials or prior access needed.
- The enumeration technique (comparing response body text) requires no special tooling and works with a single request per candidate email, well within rate limits over time.
- The timing side channel requires more samples to be statistically reliable (bcrypt work factor differences can be noisy over a network), but is a well-established class of oracle and is trivially automatable.
- Overall, this is a low-effort, highly repeatable attack pattern for an unprivileged, unauthenticated attacker.

### Recommendation
- Normalize all failure paths in `orm.CreateSession` to return one generic error (e.g., `"invalid credentials"`) for both "user not found" and "wrong password" cases, and avoid propagating raw DB errors up to `jsonAPIError`.
- Equalize timing by performing a dummy/bcrypt comparison against a fixed hash even when `FindUser` fails, so both code paths take a comparable amount of time regardless of whether the email exists.
- Consider adding jitter/minimum-delay enforcement on the `/sessions` handler independent of DB lookup outcome.

### Proof of Concept
Go handler-level integration test plan (extending `TestSessionsController_Create` in `core/web/sessions_controller_test.go`):
1. Create one real user via `app.AuthenticationProvider().CreateUser`.
2. Issue N POST requests to `/sessions` with the real email + wrong password, and N requests with random non-existent emails + any password.
3. Assert:
   - Response bodies for the two cases are different strings (currently `"Invalid password"` vs. a raw DB error string) — this demonstrates the body-based oracle.
   - Measure wall-clock time for each request category (e.g., using `time.Now()` around `client.Do(request)`), aggregate over multiple trials, and assert that the mean/median response time for "known email" requests is statistically distinguishable (e.g., consistently higher by more than a defined threshold) from "unknown email" requests — this demonstrates the timing oracle.
4. Expected (fixed-state) assertions: both response bodies should be identical and response time distributions should overlap within a fixed tolerance regardless of email validity.

### Citations

**File:** core/web/router.go (L210-215)
```go
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
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
