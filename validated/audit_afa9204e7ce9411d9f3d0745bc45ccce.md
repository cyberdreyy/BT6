### Title
Username enumeration via distinguishable error messages in `/sessions` login (`CreateSession`) - ([File: core/sessions/localauth/orm.go])

### Summary
The `CreateSession` function returns different, unredacted error strings depending on whether the submitted email exists in the `users` table, and the HTTP handler forwards these raw error messages to the client. This allows an unauthenticated attacker to enumerate valid admin/API-user emails on a Chainlink node before attempting credential stuffing.

### Finding Description
`CreateSession` first looks up the user by email with `o.FindUser(ctx, sr.Email)` [1](#0-0) . If the email does not exist, `findUser`'s `GetContext` returns the underlying SQL "no rows" error, and this raw error is returned immediately from `CreateSession` [2](#0-1) . If the email does exist but the password is wrong, execution proceeds past the (effectively always-true, since the row was already matched by email) `constantTimeEmailCompare` check and fails at `utils.CheckPasswordHash`, returning the distinct string `"Invalid password"` [3](#0-2) .

The HTTP handler `SessionsController.Create` passes this error verbatim to the client as the JSON API error body via `jsonAPIError(c, http.StatusUnauthorized, err)`, with no normalization [4](#0-3) . Additionally, before reaching `CreateSession`, the controller calls `GetUserWebAuthn(ctx, sr.Email)` and, on DB error, returns a 500 "internal Server Error", while a successful (even empty) lookup proceeds to `CreateSession` [5](#0-4) . Combined, an unauthenticated attacker can distinguish "email doesn't exist" (SQL no-rows style error) from "email exists, wrong password" (`"Invalid password"`) responses.

No middleware, rate limiter, or generic-error normalization intercepts this path — the `/sessions` POST route is intentionally unauthenticated (it is the login endpoint), so there's no auth wrapper to stop it.

### Impact Explanation
This is a username/email enumeration vulnerability against the node's local admin login endpoint. It doesn't grant direct authentication bypass, but it narrows the attack surface for credential stuffing or brute force against verified valid accounts (which may include `view`, `run`, `edit`, or `admin` role users on multi-user nodes) — increasing the practical risk of subsequent unauthorized session creation if a weak or reused password exists. This corresponds to a low/informational-severity "information disclosure aiding authentication attack" class, not a direct compromise on its own.

### Likelihood Explanation
Preconditions are minimal: unauthenticated network access to a node's `/sessions` endpoint (already the case for any login attempt). No credentials or roles required. The attack is trivially repeatable and scriptable (submit distinct emails with a fixed wrong password and observe response body differences).

### Recommendation
Return a single generic error (e.g., `"invalid credentials"`) with the same HTTP status for both "email not found" and "invalid password" cases in `CreateSession`/`SessionsController.Create`, and avoid leaking raw SQL/driver errors to callers. Consider adding constant-time/constant-latency handling (e.g., always perform a dummy password hash comparison even when the user isn't found) to also reduce timing-based enumeration, and rate-limit the `/sessions` endpoint.

### Proof of Concept
Go handler-level test plan:
1. Seed one known user `existing@example.com` with a valid hashed password.
2. POST `/sessions` with `{"email": "existing@example.com", "password": "wrongpass"}` and capture status code + response body.
3. POST `/sessions` with `{"email": "nonexistent@example.com", "password": "wrongpass"}` and capture status code + response body.
4. Assert both currently differ (e.g., one contains `"Invalid password"`, the other a SQL/no-rows-derived message) — demonstrating the enumeration, which should be flagged as failing an "indistinguishable error" invariant.
5. After the fix, assert both responses have identical status codes and identical generic error bodies (e.g., `"invalid credentials"`).

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

**File:** core/web/sessions_controller.go (L41-47)
```go
	// Does this user have 2FA enabled?
	userWebAuthnTokens, err := sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)
	if err != nil {
		sc.App.GetLogger().Errorf("Error loading user WebAuthn data: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
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
