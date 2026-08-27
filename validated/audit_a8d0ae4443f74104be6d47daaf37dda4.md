### Title
Failed OIDC claim extraction is not enforced by a `return`, allowing a broken login to still create a valid session — ([File: core/sessions/oidcauth/oidc.go])

### Summary
In `oidcAuthenticator.handleTokenExchange`, several failure branches write an HTTP error to the response but omit the `return` statement that should stop further processing. Just like the reported `_safeTransferAVAX` bug — where a failed low-level call is not actually checked, so the code proceeds as if it succeeded — here a failed extraction of the `email` claim (and a failed session-row DB insert) is logged/reported but the handler keeps running to completion, ultimately creating and persisting an authenticated session and returning HTTP 200 success.

### Finding Description
`handleTokenExchange` extracts the `email` claim from the verified OIDC ID token claims: [1](#0-0) 

If the `email` claim is missing or not a string, the handler logs an error and calls `c.String(http.StatusInternalServerError, ...)`, but it does **not** `return`. Execution falls through to role mapping, session-row creation, and session persistence using an empty `email` value: [2](#0-1) 

A second instance of the same pattern exists a few lines later: if the `INSERT INTO oidc_sessions` write fails, the handler again reports an internal error via `c.String` but does not `return`, and proceeds to set the session cookie and respond with `http.StatusOK`: [3](#0-2) 

Because Gin does not automatically stop request processing after a handler writes a response body, both of these failure paths are effectively "overlooked" — the same root-cause pattern as the low-level-call bug in the external report, where a failed operation's return status is not actually checked/acted upon, so downstream code proceeds under the false assumption that the operation succeeded.

### Impact Explanation
Continuing past the failed `email` extraction means a browser-authenticated OIDC session (`ginSession`) is created and saved with `webauth.SessionIDKey` set to a valid `clSession.ID`, and the handler ultimately returns `c.JSON(http.StatusOK, ExchangeTokenResponse{Success: true})` (the last write wins in Gin), even though the identity/audit trail (`user_email`) in `oidc_sessions` is empty. Any code path that maps that session ID back to a user (e.g. `FindUser`/session lookup) or relies on the `oidc_sessions.user_email` column for authorization would treat this as an authenticated, but effectively identity-less/cross-user-confused, session. This is a concrete authentication/audit-integrity bypass rooted in an unprivileged, internet-facing endpoint (the OIDC callback/token-exchange flow), which is exactly the class of issue in scope (node API authentication, session/token handling).

### Likelihood Explanation
This code path executes on every OIDC token exchange. An IdP or claim set lacking a top-level `email` claim (common if `email` isn't included in the granted scopes, or if a compromised/misconfigured IdP omits it) is enough to trigger the fall-through. No privileged access is required — this is reachable by any client completing the OAuth2/OIDC redirect flow to `/sessions/oidc/callback`-style endpoints handled by `handleTokenExchange`.

### Recommendation
Add explicit `return` statements immediately after each error response in `handleTokenExchange`, specifically:
- After `c.String(http.StatusInternalServerError, "Failed to get email from claims")` at [1](#0-0) .
- After `c.String(http.StatusInternalServerError, "Error creating session")` at [4](#0-3) .

More generally, audit all `handleTokenExchange`/`oidc.go` error branches to confirm each write to the `gin.Context` response is unconditionally followed by `return`, mirroring the report's recommendation to explicitly check for failure rather than silently continuing as if the operation succeeded.

### Proof of Concept
1. Configure the node with OIDC auth enabled, using an IdP/scope configuration where the ID token's claim map omits `email` (e.g., `email` scope not granted, or an IdP that nests email differently).
2. Complete the OAuth2 authorization code flow up to the callback (`POST` to the token-exchange endpoint with a valid `code`/`state`).
3. In `handleTokenExchange`, `claims["email"].(string)` fails (`ok == false`); the handler logs the error and writes `c.String(http.StatusInternalServerError, ...)` but does not return.
4. Execution continues: `IDClaimsToUserRole` succeeds using other claims, a new `oidc_sessions` row is inserted with an empty `user_email`, the session cookie is set (`ginSession.Save()`), and the final line writes `c.JSON(http.StatusOK, ExchangeTokenResponse{Success: true})`, overriding the earlier error write.
5. The client ends up with a valid, cookie-backed authenticated session tied to a session record with no `user_email`, despite the server having detected and reported a fatal claim-extraction failure.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L226-230)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L247-275)
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
```
