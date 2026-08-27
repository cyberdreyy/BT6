### Title
User enumeration via distinguishable authentication error messages in `CreateSession` - ([File: core/sessions/localauth/orm.go])

### Summary
`CreateSession` returns different, distinguishable errors depending on whether the submitted email exists in the `users` table. A nonexistent email causes `FindUser` to fail with the underlying SQL "no rows" error, while an existing email with a wrong password returns the literal string `"Invalid password"`. Both are propagated verbatim to the HTTP client as a `401` JSON error, allowing an unauthenticated attacker to enumerate registered account emails.

### Finding Description
The unauthenticated route `POST /sessions` is handled by `SessionsController.Create`, which calls `sc.App.AuthenticationProvider().CreateSession(ctx, sr)` and on any error returns `jsonAPIError(c, http.StatusUnauthorized, err)` verbatim [1](#0-0) .

Inside `CreateSession`, the first step calls `o.FindUser(ctx, sr.Email)`, which runs `SELECT * FROM users WHERE lower(email) = lower($1)` via `GetContext` [2](#0-1) . If the email does not exist, this returns the underlying SQL "no rows" error (e.g., `sql: no rows in result set`), and `CreateSession` immediately returns that error string unmodified [3](#0-2) .

If the email exists but the password is wrong, execution proceeds past the `constantTimeEmailCompare` check (which will succeed because `user.Email` was actually found) and fails at `utils.CheckPasswordHash`, returning the distinct literal string `"Invalid password"` [4](#0-3) .

These two error strings — the SQL "no rows" message vs. the literal `"Invalid password"` — are clearly distinguishable in the JSON response body returned by `jsonAPIError`, even though both are sent with the same `401 Unauthorized` status code. An attacker can therefore submit a candidate email with an arbitrary password and determine, purely from the response body text, whether that email is a registered account. The `constantTimeEmailCompare` and comment about "Do email and password check first to prevent extra database look up for MFA tokens leaking" show an intent to avoid timing/existence leaks, but the actual implementation still leaks account existence through message content when the email lookup itself fails before reaching that comparison.

### Impact Explanation
This maps to a user/account enumeration weakness (information disclosure of registered admin/API credentials existence) against the node's local authentication `/sessions` endpoint. It does not by itself grant session or credential access, but it materially aids credential-stuffing or targeted brute-force attacks against the node's admin API by confirming valid usernames.

### Likelihood Explanation
No preconditions or credentials are required — any network client that can reach `POST /sessions` can perform this probe, and it is trivially repeatable (one request per email guess).

### Recommendation
Normalize the error path in `CreateSession` so that a nonexistent email and a valid email with incorrect password return identical error values/messages (and ideally similar timing) — e.g., perform a dummy password-hash comparison against a static/fixed hash when the user lookup fails, and return a single generic error type/string (e.g., `"invalid credentials"`) for both cases before any WebAuthn/2FA-specific error branches.

### Proof of Concept
Go handler-level test in `core/sessions/localauth/orm_test.go` or `core/web/sessions_controller_test.go`:
1. Seed one user `real@example.com` with a known password hash.
2. Case A: call `CreateSession`/`POST /sessions` with `email=real@example.com`, `password=wrong` → capture error string/body.
3. Case B: call with `email=doesnotexist@example.com`, `password=wrong` → capture error string/body.
4. Assert both requests return HTTP `401`, but observe that the JSON error body text differs (SQL "no rows" derived message vs. `"Invalid password"`), proving the messages are distinguishable and thus leak email existence.

### Citations

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

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
