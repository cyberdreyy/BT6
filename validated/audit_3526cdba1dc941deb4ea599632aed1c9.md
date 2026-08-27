## Title
Authentication flow continues execution after failed email-claim assertion, granting a valid session despite unhandled error - (File: `core/sessions/oidcauth/oidc.go`)

### Summary
In `handleTokenExchange`, the OIDC token-exchange handler checks a type assertion result but fails to abort the request on failure, mirroring the reported bug class where a failed check is silently swallowed and processing continues to completion (mint/withdraw collateral) instead of halting.

### Finding Description
In `(*oidcAuthenticator).handleTokenExchange`, after ID token verification and claim extraction succeed, the handler extracts the `email` claim via a type assertion: [1](#0-0) 

When the assertion fails (`ok == false`), the code logs the error and writes an HTTP 500 response body with `c.String(...)`, but — unlike every other error branch in this same function (lines 167-171, 189-196, 200-204, 208-212, 215-219, 221-225, 241-245, 266-271) — it does **not** `return`. Execution falls through and continues the rest of the authentication flow: [2](#0-1) 

As a result, even though the email claim could not be retrieved:
- A new `oidc_sessions` row is inserted with an empty/zero-value `email` at line 254.
- An `AuthLoginSuccessNo2FA` audit event is recorded as if login succeeded (line 262).
- The session cookie is set via `ginSession.Set(webauth.SessionIDKey, clSession.ID)` and saved (lines 265-266).
- A final `c.JSON(http.StatusOK, ExchangeTokenResponse{Success: true})` is written on top of the already-written 500 response body (line 273).

This is directly analogous to the reported pattern: a return value/condition is checked, the failure is even logged, but the code does not halt on the failure and instead proceeds to complete the sensitive operation (here, persisting a session and issuing an authenticated cookie) as if the check had succeeded.

### Impact Explanation
An unprivileged client (any user completing the OIDC redirect flow whose ID token/userinfo response lacks a top-level `email` claim, e.g. some OIDC providers only return `email` when a specific scope/claim is granted, or a misbehaving/attacker-influenced identity provider) still ends up with:
- A persisted, DB-backed session with a real session ID and the previously-computed RBAC role (admin/edit/run/view, derived from `idClaims`, which is validated separately from `email`).
- A valid authentication cookie set in their browser, usable for subsequent authenticated API calls via `AuthorizedUserWithSession`.

The response returned to the caller is also inconsistent/corrupted (a 500 status with a concatenated body containing both the error string and the success JSON), which can confuse the frontend into believing the login failed when a working session was actually created server-side.

### Likelihood Explanation
This code path is reached on every call to `POST /oidc-exchange`, which is exposed to any client completing the OIDC redirect flow — it does not require any special privilege beyond initiating the standard sign-in dance. Reachability depends only on the identity provider's token/userinfo response omitting the `email` field, which is plausible in real-world OIDC configurations.

### Recommendation
Add a `return` immediately after writing the error response in the `ok` check for the `email` claim, matching the pattern used in every other error branch of `handleTokenExchange`:
```go
email, ok := claims["email"].(string)
if !ok {
    oi.lggr.Errorf("Failed to get email from claims")
    c.String(http.StatusInternalServerError, "Failed to get email from claims")
    return
}
```
Additionally consider treating `email` as a required claim validated earlier (similar to `ExtractIDClaimValues`) so authentication cannot proceed without it.

### Proof of Concept
1. Configure/point the node at an OIDC provider (or intercept the token exchange) such that the ID token/userinfo claims map returned to `handleTokenExchange` does not contain an `email` key (or it is a non-string type).
2. Complete the normal `/oidc-login` → provider redirect → `/oidc-exchange` flow with a valid `code`/`state`.
3. Observe that despite the logged error and the 500 status written for the missing email, the server still inserts a row into `oidc_sessions`, sets the session cookie, and emits `AuthLoginSuccessNo2FA`.
4. Use the session cookie set in the response to make authenticated requests — they succeed with the role derived from the ID token's group claims, confirming a functioning session was created despite the failed check.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L226-230)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L247-276)
```go
	// Save new user authenticated clSession and role to oidc_sessions table
	// Sessions are set to expire after the duration + creation date elapsed
	clSession := clsessions.NewSession()
	_, err = oi.ds.ExecContext(
		ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		clSession.ID,
		strings.ToLower(email),
		role,
	)
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		c.String(http.StatusInternalServerError, "Error creating session")
	}

	oi.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": email})

	// save session
	ginSession.Set(webauth.SessionIDKey, clSession.ID)
	err = ginSession.Save()
	if err != nil {
		oi.lggr.Errorf("failed to saved session %v", err)
		c.String(http.StatusInternalServerError, "Authentication failed")
		return
	}

	c.JSON(http.StatusOK, ExchangeTokenResponse{
		Success: true,
	})
}
```
