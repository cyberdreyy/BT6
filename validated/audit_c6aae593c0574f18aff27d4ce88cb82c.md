### Title
Email/password/user-not-found errors from `CreateSession` are distinguishable, enabling account email enumeration - ([File: core/sessions/localauth/orm.go])

### Summary
`orm.CreateSession` returns distinct, literal error strings ("Invalid email", "Invalid password", "MFA Error") that are forwarded verbatim to the unauthenticated HTTP client via `SessionsController.Create`. Additionally, an unknown email causes `FindUser` to fail before reaching the "Invalid email" branch, producing yet another distinguishable error (a raw SQL "no rows" error), giving attackers three distinguishable states instead of one generic response.

### Finding Description
`POST /sessions` is handled by `SessionsController.Create` in `core/web/sessions_controller.go`, which calls `sc.App.AuthenticationProvider().CreateSession(ctx, sr)` and on error does `jsonAPIError(c, http.StatusUnauthorized, err)` [1](#0-0) , passing the raw error message from `CreateSession` straight into the HTTP response body with no redaction or generic wrapping.

Inside `orm.CreateSession` (`core/sessions/localauth/orm.go`):
- If `FindUser` (a case-insensitive email lookup) fails — i.e., the email doesn't exist in the DB — the function returns that raw DB error (e.g. `sql: no rows in result set`) directly, before any constant-time comparison happens: [2](#0-1) .
- If the email is found but doesn't match (which, given the lookup above, can really only diverge on casing or timing edge-cases) it returns `"Invalid email"`: [3](#0-2) .
- If password doesn't match, it returns `"Invalid password"`: [4](#0-3) .
- If further along, MFA-related errors return `"MFA Error"`.

So even though the code comment at line 152 claims the ordering is meant to prevent leaking whether an account has MFA enabled, the three states (no-such-email SQL error vs "Invalid email" vs "Invalid password") are trivially distinguishable by an unauthenticated caller, and are returned as literal, differing text/JSON error bodies. There is no rate limiting or generic-error normalization visible in `SessionsController.Create` that would mask this distinction before the response reaches the client.

### Impact Explanation
This allows an unauthenticated attacker to enumerate valid admin/API-user email addresses on a Chainlink node by sending guessed emails with a wrong password and observing the differing error text ("Invalid password" for valid, distinguishable DB error for invalid). This maps to a low-severity information-disclosure / user-enumeration finding — it does not directly grant authentication bypass or credential exposure but meaningfully lowers the cost of targeted credential-stuffing/brute-force attacks against the node's admin/API accounts (a Chainlink node normally has a very small, known set of users, so this narrows attacker effort considerably).

### Likelihood Explanation
No preconditions are required beyond network access to the node's `/sessions` endpoint — the caller is fully unauthenticated. The difference in error messages is deterministic and repeatable via simple HTTP requests, making the enumeration trivially automatable.

### Recommendation
Return a single generic error (e.g., "invalid credentials") and identical HTTP status for all three failure paths — unknown email, wrong password, and any internal WebAuthn/MFA failure — in both `orm.CreateSession` and at the `SessionsController.Create` response layer, so error text and status code carry no information about which stage failed. Consider also using constant-time/constant-latency logic uniformly across the unknown-email and wrong-password paths (currently the unknown-email path short-circuits before the constant-time password check even runs, which could also introduce a timing side-channel).

### Proof of Concept
Go handler-level integration test plan (extending `core/web/sessions_controller_test.go` / `core/sessions/localauth/orm_test.go`):
1. Seed one known user with a known password.
2. Case A: POST `/sessions` with a non-existent email + arbitrary password → capture status code and response body.
3. Case B: POST `/sessions` with the known user's email + wrong password → capture status code and response body.
4. Assert both cases currently produce **different** response bodies (demonstrating the vulnerability): Case A surfaces a DB "no rows" style error while Case B surfaces `"Invalid password"`.
5. After the fix, assert both cases return an identical generic status code (e.g., 401) and identical generic body text, with no distinguishing content.

### Citations

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
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

**File:** core/sessions/localauth/orm.go (L152-157)
```go
	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}
```

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```
