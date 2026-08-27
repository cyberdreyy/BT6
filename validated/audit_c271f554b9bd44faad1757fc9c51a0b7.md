### Title
User Enumeration via Inconsistent Error Responses in `CreateSession` - ([File: core/sessions/localauth/orm.go])

### Summary
`orm.CreateSession` returns different, distinguishable error content depending on whether an account exists or the password is simply wrong, allowing an unauthenticated caller to enumerate valid email addresses on the `POST /v2/sessions` endpoint. The intended "Invalid email"/"Invalid password" constant-time comparison at lines 154-162 is effectively dead code in the normal flow because the actual observable divergence happens earlier, at the `FindUser` lookup.

### Finding Description
`SessionsController.Create` (core/web/sessions_controller.go, lines 29-68) binds an unauthenticated `POST /v2/sessions` request body into a `SessionRequest` and calls `sc.App.AuthenticationProvider().CreateSession(ctx, sr)` with no prior authentication. [1](#0-0) 

Inside `CreateSession`, the very first step is `o.FindUser(ctx, sr.Email)`, a DB lookup keyed on the submitted email: [2](#0-1) 
If the account does not exist, `FindUser` returns the raw driver error (e.g., `sql: no rows in result set`) and `CreateSession` returns it unmodified. If the account does exist but the password is wrong, execution instead reaches: [3](#0-2) 
returning the fixed string `"Invalid password"`.

Because `findUser`'s SQL already filters by `lower(email) = lower($1)`, the `constantTimeEmailCompare` check at line 154 will essentially always pass whenever a row was found — it can never meaningfully diverge in the normal flow, so it does not provide the anti-enumeration protection its comment claims ("Do email and password check first to prevent extra database look up for MFA tokens leaking..."). The real, exploitable divergence is between the *"user not found"* branch (raw SQL error text) and the *"wrong password"* branch (`"Invalid password"`).

`SessionsController.Create` propagates this error text verbatim to the HTTP client via `jsonAPIError(c, http.StatusUnauthorized, err)`: [1](#0-0) 
No middleware, presenter, or redaction layer normalizes these messages before they reach the caller — the `/v2/sessions` login endpoint is unauthenticated by design, so any external caller can probe it freely.

### Impact Explanation
An unauthenticated attacker can distinguish "account does not exist" from "account exists but wrong password" purely from the JSON error body returned by `POST /v2/sessions`, enabling systematic enumeration of valid operator/admin email addresses on the node. This is an information-disclosure issue (credential/account confidentiality violation) that facilitates targeted credential-stuffing or phishing against confirmed valid accounts, and leaks internal DB error strings which can hint at implementation details. It does not by itself grant privilege escalation, since MFA presence (WebAuthn challenge) is only observable *after* a correct password is supplied, which requires already-valid credentials.

### Likelihood Explanation
No preconditions or credentials are needed — the endpoint is the login endpoint itself. The attack is trivially repeatable: an attacker can script a list of candidate emails against `POST /v2/sessions` with an arbitrary password and bucket responses by error text (`sql: no rows in result set`-style vs. `"Invalid password"`). This is fully deterministic and does not depend on timing side channels.

### Recommendation
Return a single, generic, identical error (message and structure) for both "account not found" and "wrong password" cases in `CreateSession`, and ensure the underlying DB error is never propagated to the client. E.g., wrap the `FindUser` error path to fall through to the same `"Invalid email or password"` message/audit path used for wrong-password, instead of returning the raw error at core/sessions/localauth/orm.go lines 144-148. Also remove/rename the now-misleading `constantTimeEmailCompare` comment or repurpose it to guard the actual generic response path.

### Proof of Concept
Go handler-level integration test plan:
1. Seed one existing user (`existing@example.com` / known password).
2. Send `POST /v2/sessions` with `{"email":"existing@example.com","password":"wrongpass"}` and capture response body/status.
3. Send `POST /v2/sessions` with `{"email":"doesnotexist@example.com","password":"wrongpass"}` and capture response body/status.
4. Assert both responses currently differ (body text: `"Invalid password"` vs. raw SQL error string), proving the enumeration oracle.
5. After the fix, assert both responses are byte-for-byte identical in body and status code.

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
