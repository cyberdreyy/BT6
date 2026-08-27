### Title
User enumeration via distinguishable error messages and timing in `SessionsController.Create` / `orm.CreateSession` - ([File: core/sessions/localauth/orm.go])

### Summary
`orm.CreateSession` first looks up the user by email with `FindUser`, and returns immediately with a database-lookup error if the email does not exist — never reaching the constant-time email compare or the (comparatively slow) `bcrypt` password check. When the email does exist but the password is wrong, the handler additionally performs a `bcrypt.CompareHashAndPassword` call before returning a different error string ("Invalid password" vs. the "Invalid email"/`FindUser` error). This produces both a content and a timing side channel that lets an unauthenticated caller distinguish "email exists" from "email does not exist."

### Finding Description
`SessionsController.Create` (`core/web/sessions_controller.go:29-68`) forwards attacker-supplied `email`/`password` straight to `AuthenticationProvider().CreateSession`, then returns whatever error it gets via `jsonAPIError(c, http.StatusUnauthorized, err)` [1](#0-0) , and `jsonAPIError` serializes `err.Error()` verbatim into the JSON body [2](#0-1) .

In `orm.CreateSession` (`core/sessions/localauth/orm.go:144-162`):
- It first calls `o.FindUser(ctx, sr.Email)`; if the email is not registered, this returns early with a DB-level error (e.g. sql "no rows" wrapping), before any password/email comparison work happens [3](#0-2) .
- If the user is found, the code does a constant-time email compare and, if that passes, returns `"Invalid password"` on a bad password only after calling `utils.CheckPasswordHash` (a `bcrypt` comparison, which is intentionally CPU-expensive) [4](#0-3) .

The `constantTimeEmailCompare` mitigation only protects the comparison *after* `FindUser` has already succeeded — it does nothing for the "no such user" case, which short-circuits before that code path is reached at all. As a result:
1. **Content signal**: the JSON error body differs ("Invalid email" path vs. `FindUser`'s underlying DB error vs. "Invalid password"), all surfaced verbatim to the client.
2. **Timing signal**: a request against a valid email always pays the cost of a `bcrypt` hash comparison (`CheckPasswordHash`), while a request against a nonexistent email returns immediately after a DB miss — a measurable and repeatable latency difference.

### Impact Explanation
This lets any unauthenticated network client enumerate valid node-admin usernames/emails against `/sessions` by observing response body text and/or response latency, without needing any credentials. Maps to Chainlink's "user enumeration"/"authentication weakness" impact class — it does not itself grant access, but it materially assists targeted credential-stuffing and brute-force attacks against `AUTHENTICATION_SOUNDNESS`.

### Likelihood Explanation
Precondition is only unauthenticated network access to `/sessions`, exactly as scoped. The signal is deterministic and repeatable (same status code path each time, message differs by design; bcrypt timing difference is consistently ~tens of milliseconds and easily measured over many samples), so likelihood of successful enumeration is high.

### Recommendation
- Return a single generic error/message and status code for all three failure branches (no such user, wrong email casing, wrong password) — do not propagate `FindUser`'s underlying error text or a distinct "Invalid password"/"Invalid email" string to the client.
- Equalize timing by always performing a dummy/constant-cost `bcrypt` comparison (against a fixed dummy hash) even when the user is not found, so the "user not found" path takes comparable time to the "user found, wrong password" path.

### Proof of Concept
Extend `TestSessionsController_Create` (`core/web/sessions_controller_test.go`) with a table-driven test that, for `{"incorrect pwd", ...}` and `{"incorrect email", ...}`:
1. Asserts both cases return identical `resp.StatusCode` (already true — both 401) **and** identical error message body content (currently fails: bodies differ, e.g. contains "Invalid password" vs. a different string derived from `FindUser`'s error).
2. Measures wall-clock time for N repeated requests to each variant and asserts the average latency difference is within a small tolerance (currently expected to fail because the wrong-password path runs `bcrypt.CompareHashAndPassword` while the wrong-email path returns immediately after a DB lookup miss).

### Citations

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

**File:** core/sessions/localauth/orm.go (L152-162)
```go
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
