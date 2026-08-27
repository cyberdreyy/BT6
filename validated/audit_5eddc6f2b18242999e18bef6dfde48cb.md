### Title
Missing `return` after failed email-claim extraction lets `handleTokenExchange` create an authenticated session with an empty `user_email` - ([File: core/sessions/oidcauth/oidc.go])

### Summary
In `handleTokenExchange`, when the `email` claim is missing or not a string, the handler logs the error and writes an HTTP 500 response but does not `return`, so execution falls through and creates/saves a valid `oidc_sessions` row and session cookie with an empty `user_email`. This lets any user who completes a real OIDC login without an `email` claim obtain a fully authenticated session (role assigned per group claims) while the client-visible response indicates failure.

### Finding Description
At [1](#0-0) , the code does:
```go
email, ok := claims["email"].(string)
if !ok {
    oi.lggr.Errorf(...)
    c.String(http.StatusInternalServerError, "Failed to get email from claims")
}
```
There is no `return` statement. Execution continues past this block to [2](#0-1) , which computes the RBAC role from the already-verified ID token group claims and inserts a new row into `oidc_sessions` with `user_email = strings.ToLower(email)` where `email` is the Go zero value `""`. The gin session cookie is then set and saved at [3](#0-2) , and a `Success: true` `ExchangeTokenResponse` JSON is written at the end — even though gin already committed a 500 status/body earlier, the session/cookie side effects have already occurred server-side.

Because the OIDC ID token has already passed `Verifier.Verify` (real IdP-signed token) at [4](#0-3) , any legitimately-authenticated OIDC principal (e.g., in a multi-tenant/federated IdP where `email` scope/claim is optional or the account doesn't have it configured) that belongs to one of the configured RBAC group claims can complete this flow and obtain a working session with role privileges, without ever producing a valid `email` claim. All such users collapse to the same `user_email = ""` value in `oidc_sessions`.

This directly affects `ClearNonCurrentSessions`, invoked from `core/web/user_controller.go`, which deletes rows via `DELETE FROM oidc_sessions WHERE lower(user_email) = lower($1) AND id != $2` ( [5](#0-4) ). Since the deleting user's own email is looked up from `oidc_sessions` by session ID first ( [6](#0-5) ), a user whose session has `user_email = ""` will have this query match and terminate every other session in the table that also has an empty `user_email` — i.e., any other distinct user account that also lacks an email claim. This is cross-user session termination triggered by an unrelated, lower-privileged/self-controlled identity, not merely acting on their own sessions.

### Impact Explanation
- Authentication bypass of the intended fail-closed control: a token-exchange request that should be rejected (missing required `email` claim) instead succeeds in creating a valid, role-bearing session cookie.
- Cross-user denial of service: any authenticated OIDC user without an email claim can invalidate the sessions of every other distinct OIDC user that also lacks an email claim, via the legitimate `ClearNonCurrentSessions` code path, due to the shared `""` email value collision.
- This maps to the Chainlink bounty impact classes of authentication/authorization bypass and unauthorized action affecting another user's session/access.

### Likelihood Explanation
Requires the attacker to be able to complete a legitimate OIDC authorization-code flow against the configured IdP and be a member of at least one configured RBAC claim group (Admin/Edit/Run/Read), but crucially does **not** require them to have a valid `email` claim — a condition plausible in multi-tenant IdPs, service accounts, or IdPs where the `email` scope is optional/not granted. No admin, database, or host access is needed; it only depends on the node's OIDC login flow being reachable (`/oidc-login`, `/oidc-exchange`), which is exposed to any client capable of reaching the node's HTTP API. This is fully repeatable and deterministic given such an account.

### Recommendation
Add `return` immediately after the `c.String(http.StatusInternalServerError, ...)` call in the email-extraction failure branch so that processing halts before role mapping, session insertion, and cookie persistence occur:
```go
email, ok := claims["email"].(string)
if !ok {
    oi.lggr.Errorf("Failed to get email from claims")
    c.String(http.StatusInternalServerError, "Failed to get email from claims")
    return
}
```
Additionally, consider rejecting empty-string emails explicitly, and treating `email` as a uniqueness/authorization key with a non-empty constraint (e.g., DB-level `NOT NULL`/non-empty check) so that `ClearNonCurrentSessions` cannot match unrelated accounts via a shared empty value.

### Proof of Concept
Handler-level integration test plan (Go, using `httptest` + a mocked/stubbed `oidc.Provider`/`Verifier` returning a signed token whose claims include a valid RBAC group claim but omit `email`):
1. Configure `oidcAuthenticator` with a fake `provider`/`oauth2Config` (or fake `Verifier`) that succeeds verification and returns claims `{"<claimName>": ["<EditClaim>"]}` with no `"email"` key.
2. Simulate `handleSignIn` to populate session `state`.
3. POST to `/oidc-exchange` with a matching `state` and a code that the fake oauth2 exchange resolves successfully.
4. Assert:
   - HTTP response status is `500` (current fall-through behavior).
   - Despite the 500, query `oidc_sessions` table and assert a row was inserted with `user_email = ''` (demonstrating the bug).
   - Assert the gin session cookie now contains a valid `webauth.SessionIDKey` mapping to that session row (i.e., `AuthorizedUserWithSession` succeeds for that session ID with a role).
5. Repeat steps 1-4 for a second distinct fake identity (different `sub`), also without `email`, producing a second `oidc_sessions` row with `user_email = ''`.
6. Call `ClearNonCurrentSessions(ctx, session1.ID)` and assert that `session2` row is deleted, proving cross-user session termination.
7. After applying the `return` fix, re-run steps 1-4 and assert the handler returns `500` with **no** row inserted into `oidc_sessions` and no session cookie set.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L207-212)
```go
	idToken, err := oi.provider.Verifier(oi.oidcConfig).Verify(ctx, rawIDToken)
	if err != nil {
		oi.lggr.Errorf("Failed to verify ID token: %v", err)
		c.String(http.StatusInternalServerError, "Failed to verify ID token")
		return
	}
```

**File:** core/sessions/oidcauth/oidc.go (L226-230)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L234-256)
```go
	role, err := oi.IDClaimsToUserRole(
		idClaims,
		oi.config.AdminClaim(),
		oi.config.EditClaim(),
		oi.config.RunClaim(),
		oi.config.ReadClaim(),
	)
	if err != nil {
		oi.lggr.Errorf("Failed to map configured RBAC role name against received list of group claims: %v", err)
		c.String(http.StatusBadRequest, "No matching role within attested user group claims")
		return
	}

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
```

**File:** core/sessions/oidcauth/oidc.go (L264-271)
```go
	// save session
	ginSession.Set(webauth.SessionIDKey, clSession.ID)
	err = ginSession.Save()
	if err != nil {
		oi.lggr.Errorf("failed to saved session %v", err)
		c.String(http.StatusInternalServerError, "Authentication failed")
		return
	}
```

**File:** core/sessions/oidcauth/oidc.go (L441-449)
```go
// ClearNonCurrentSessions removes other oidc_sessions for the user tied to sessionID.
func (oi *oidcAuthenticator) ClearNonCurrentSessions(ctx context.Context, sessionID string) error {
	var email string
	if err := oi.ds.GetContext(ctx, &email, "SELECT user_email FROM oidc_sessions WHERE id = $1", sessionID); err != nil {
		return err
	}
	_, err := oi.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE lower(user_email) = lower($1) AND id != $2", email, sessionID)
	return err
}
```
