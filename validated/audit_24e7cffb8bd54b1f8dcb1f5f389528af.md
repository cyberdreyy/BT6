Confirmed vulnerability. The `handleTokenExchange` function extracts `claims["email"]` and uses it directly as the session's bound identity without ever checking `email_verified` (or any equivalent trust signal) on that claim.

### Title
Missing `email_verified` check on OIDC `email` claim allows session/identity impersonation via `oidc_sessions` - ([File: core/sessions/oidcauth/oidc.go])

### Summary
`handleTokenExchange` reads `claims["email"]` straight from the verified ID token and inserts it as `user_email` in `oidc_sessions` without checking `claims["email_verified"]`. Any IdP account able to set an arbitrary (even self-supplied/unverified) `email` claim can therefore bind an authenticated OIDC session to a victim's email address.

### Finding Description
In `handleTokenExchange` [1](#0-0) , the code does:
```go
email, ok := claims["email"].(string)
```
and never inspects `claims["email_verified"]`. The signature/issuer of the ID token is verified via `oi.provider.Verifier(oi.oidcConfig).Verify(ctx, rawIDToken)` [2](#0-1) , so the token cryptographically comes from the configured IdP — but that only proves the *issuer* signed it, not that the IdP itself verified the *email address* it put inside. Many IdPs (and definitely any IdP account the attacker fully controls, per the precondition) will happily issue tokens with `email_verified: false` or omit the flag while still including an arbitrary `email` claim value (e.g., a self-registered account using a victim's email string, or a social-login provider that doesn't verify email ownership).

The role for the new session is computed independently from the attacker's own group claims via `IDClaimsToUserRole` [3](#0-2) , but the row inserted into `oidc_sessions` binds that attacker-controlled role to the victim's email:
```go
"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())"
``` [4](#0-3) 

This email is then trusted downstream as the authenticated identity: `AuthorizedUserWithSession` reads `user_email` straight out of `oidc_sessions` and returns it as the session's `clsessions.User.Email` [5](#0-4) , `ClearNonCurrentSessions` operates by matching on that email [6](#0-5) , and the audit log records the login under that email via `oi.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": email})` [7](#0-6) . Any feature that keys authorization, ownership, or auditing decisions off the session's `Email` field (rather than a stable, verified subject identifier) is thus exposed to email spoofing.

### Impact Explanation
This is an authentication-soundness break: an unprivileged attacker with only a self-created IdP account can mint a valid Chainlink node session that is *labeled* with an arbitrary victim email (e.g. an existing local admin's email) while the session itself is created and authenticated purely through the attacker's own OIDC flow. This falls into the "authentication bypass / request impersonation / cross-user response confusion" bounty class — it lets the attacker impersonate a specific user identity in the node's session/audit records and in any downstream logic keyed by email, without needing to know or control that user's actual credentials.

### Likelihood Explanation
Preconditions are exactly what the question specifies: the attacker needs an account on the configured OIDC IdP that permits setting/self-declaring the `email` claim value without proof of ownership (common on IdPs supporting self-registration or certain social logins) — no admin or local DB access is required. The flow is a single, deterministic HTTP round trip through the standard `/oidc-login` → IdP → `/oidc-exchange` sequence [8](#0-7) , fully repeatable for any target email string.

### Recommendation
Before trusting `claims["email"]` for identity binding, require and check `claims["email_verified"] == true` (per OIDC spec `email_verified` claim), and reject the exchange (return an error) if it is false or missing. Prefer keying the session's stable identity on the immutable `sub` claim rather than the mutable/possibly-unverified `email` claim, using `email` only as a display attribute once verified.

### Proof of Concept
Add a table-driven unit test around `handleTokenExchange` (or a helper extracted from it) that:
1. Constructs `claims := map[string]any{"email": "victim-admin@company.com", "email_verified": false, oi.config.ClaimName(): []string{"admin_group"}}`.
2. Feeds these claims through the code path that extracts `email` (currently `claims["email"].(string)` at `core/sessions/oidcauth/oidc.go:226`) and asserts that the current implementation still returns `ok == true` and proceeds to insert into `oidc_sessions` with `user_email = "victim-admin@company.com"`.
3. Asserts this is a bug: expected behavior is that when `email_verified` is `false` (or absent), `handleTokenExchange` should reject the exchange (HTTP 400/403) instead of creating a session, e.g. `require.Equal(t, http.StatusBadRequest, w.Code)` and `require.False(t, sessionInsertedWithEmail(db, "victim-admin@company.com"))`.
4. A companion positive test with `email_verified: true` should still succeed, confirming the fix only blocks the unverified case.

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

**File:** core/sessions/oidcauth/oidc.go (L226-231)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
	oi.lggr.Tracef("Received and validated ID claims: %v\n", idClaims)
```

**File:** core/sessions/oidcauth/oidc.go (L234-245)
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
```

**File:** core/sessions/oidcauth/oidc.go (L250-256)
```go
	_, err = oi.ds.ExecContext(
		ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		clSession.ID,
		strings.ToLower(email),
		role,
	)
```

**File:** core/sessions/oidcauth/oidc.go (L262-262)
```go
	oi.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": email})
```

**File:** core/sessions/oidcauth/oidc.go (L358-379)
```go
		var foundSession struct {
			UserEmail string
			UserRole  clsessions.UserRole
			Valid     bool
		}
		if err := tx.GetContext(ctx, &foundSession,
			"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM oidc_sessions WHERE id = $1",
			sessionID, oi.config.SessionTimeout().Duration(),
		); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				return clsessions.ErrUserSessionExpired
			}
			return err
		}
		if !foundSession.Valid {
			// Sessions expired, purge
			return clsessions.ErrUserSessionExpired
		}
		foundUser = clsessions.User{
			Email: foundSession.UserEmail,
			Role:  foundSession.UserRole,
		}
```

**File:** core/sessions/oidcauth/oidc.go (L442-449)
```go
func (oi *oidcAuthenticator) ClearNonCurrentSessions(ctx context.Context, sessionID string) error {
	var email string
	if err := oi.ds.GetContext(ctx, &email, "SELECT user_email FROM oidc_sessions WHERE id = $1", sessionID); err != nil {
		return err
	}
	_, err := oi.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE lower(user_email) = lower($1) AND id != $2", email, sessionID)
	return err
}
```

**File:** core/sessions/oidcauth/oidc.go (L658-663)
```go
func (oi *oidcAuthenticator) ExtendRouter(api *gin.RouterGroup) error {
	api.GET("/oidc-enabled", oi.handleCheckEnabled)
	api.GET("/oidc-login", oi.handleSignIn)
	api.POST("/oidc-exchange", oi.handleTokenExchange)

	return nil
```
