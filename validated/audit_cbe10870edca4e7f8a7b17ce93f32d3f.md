Confirmed: `jsonAPIError` at `core/web/helpers.go:21-29` puts `err.Error()` directly into the JSON response body via `models.NewJSONAPIErrorsWith(err.Error())`, so whatever error `CreateSession` returns is echoed to the client verbatim.

### Title
User enumeration via distinguishable error/timing between missing-email and wrong-password paths in `CreateSession` - ([File: core/sessions/localauth/orm.go])

### Summary
`CreateSession` performs `FindUser` (a raw DB lookup) before any constant-time comparison, and returns the DB error directly ("no rows"/DB error text) when the email doesn't exist, versus `"Invalid password"` when the email exists but the password is wrong. Because the expensive bcrypt `CheckPasswordHash` call only executes on the "email exists" path, both response content and response timing differ measurably between existing and non-existing accounts.

### Finding Description
In `core/sessions/localauth/orm.go`:
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err   // raw DB error returned immediately for non-existent email
	}
	...
	if !constantTimeEmailCompare(...) { return "", pkgerrors.New("Invalid email") }
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		return "", pkgerrors.New("Invalid password")  // bcrypt compare already executed
	}
``` [1](#0-0) 

`FindUser`/`findUser` issues `SELECT * FROM users WHERE lower(email) = lower($1)` and returns whatever `sqlx`/`sql` error results (e.g., `sql.ErrNoRows`) when no row matches. [2](#0-1) 

The `constantTimeEmailCompare` helper is only reached when `FindUser` succeeds, i.e., only when the email exists (it compares `sr.Email` to the found `user.Email`, which will always match after a successful lookup by that same email) — so it never actually protects the "does this email exist" boundary; it's a no-op safeguard against a hypothetical case-mismatch, not against enumeration. [3](#0-2) 

The controller `SessionsController.Create` passes `err.Error()` straight to the client through `jsonAPIError`, so the literal text differs ("Invalid password" vs. a SQL "no rows" style error) for the two cases: [4](#0-3) [5](#0-4) 

Additionally, for an existing email, `utils.CheckPasswordHash` runs a bcrypt comparison (deliberately slow, ~50-100ms typically) before returning; for a non-existent email, the function returns immediately after the DB miss, with no bcrypt work. This produces a timing side channel independent of the error text.

The endpoint `/sessions` (`SessionsController.Create`) is registered unauthenticated and rate-limited only by generic unauthenticated rate limits, not enumeration-specific protections: [6](#0-5) 

### Impact Explanation
This allows an unauthenticated attacker to distinguish valid registered emails from invalid ones by response content and/or timing, enabling targeted credential-stuffing and phishing against confirmed valid accounts. This maps to a low/informational "user enumeration" impact class rather than direct authentication bypass or key/secret disclosure — it does not itself allow login, privilege escalation, or fund movement, but it weakens the authentication surface and aids further attacks.

### Likelihood Explanation
The precondition is only network access to `/sessions`, no credentials required. The generic unauthenticated rate limiter throttles brute-force volume but does not eliminate single/low-volume timing or error-content differential probes. The difference is deterministic and reproducible per request, not dependent on race conditions, making repeated confirmation of any email straightforward (bounded by rate limiting, not the timing bug itself).

### Recommendation
Always perform a dummy/constant-cost bcrypt comparison (against a fixed dummy hash) when `FindUser` returns no matching row, before returning, and unify the error returned/logged for both "email not found" and "password mismatch" to a single generic message (e.g., `"invalid credentials"`) so response body content and timing are indistinguishable for existing vs. non-existing emails.

### Proof of Concept
Go unit test plan (table-driven, similar to `TestORM_CreateSession` in `core/sessions/localauth/orm_test.go`):
1. Create one real user with a known password.
2. Call `orm.CreateSession` with (a) the real email + wrong password, and (b) a random non-existent email + any password.
3. Assert: `err.Error()` differs between (a) and (b) today (demonstrating the content leak) — e.g. (a) returns `"Invalid password"`, (b) returns a DB "no rows" style error.
4. Wrap the two calls with `time.Now()`/`time.Since()` timing measurement across N iterations and assert a statistically significant timing gap between (a) (includes bcrypt compare) and (b) (returns immediately after DB miss), e.g. using a fake `sqlutil.DataSource` mock that returns `sql.ErrNoRows` instantly for (b) vs. a stub returning a stored bcrypt hash for (a).
5. After remediation, assert both paths return the same generic error string and timing difference falls within a small, bounded tolerance (e.g., via a stub bcrypt dummy-compare added for the not-found path).

### Citations

**File:** core/sessions/localauth/orm.go (L55-59)
```go
func (o *orm) findUser(ctx context.Context, email string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE lower(email) = lower($1)"
	err = o.ds.GetContext(ctx, &user, sql, email)
	return
}
```

**File:** core/sessions/localauth/orm.go (L144-162)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
	lggr := o.lggr.With("user", user.Email)
	lggr.Debugw("Found user")

	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/sessions/localauth/orm.go (L232-241)
```go
const constantTimeEmailLength = 256

func constantTimeEmailCompare(left, right string) bool {
	length := mathutil.Max(constantTimeEmailLength, len(left), len(right))
	leftBytes := make([]byte, length)
	rightBytes := make([]byte, length)
	copy(leftBytes, left)
	copy(rightBytes, right)
	return subtle.ConstantTimeCompare(leftBytes, rightBytes) == 1
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

**File:** core/web/router.go (L207-218)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
}
```
