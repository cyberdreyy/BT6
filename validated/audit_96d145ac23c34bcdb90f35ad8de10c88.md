### Title
OIDC session role is cached at login and never revalidated against the identity provider, granting stale/elevated RBAC privileges for the life of the session - ([File: core/sessions/oidcauth/oidc.go])

### Summary
Chainlink's OIDC authentication provider maps identity-provider group claims to a local RBAC role once, at token-exchange time, and persists that role in the `oidc_sessions` table for the life of the session/cookie. Unlike the LDAP provider, which explicitly documents and implements periodic re-sync of cached roles against the upstream directory (`UpstreamSyncInterval`), the OIDC provider has no equivalent mechanism: the only background job touching `oidc_sessions` is a reaper that deletes stale rows, it never re-checks or updates the cached role.

### Finding Description
On successful OIDC login, `handleTokenExchange` maps the IdP's group claims to a role via `IDClaimsToUserRole` and writes it into `oidc_sessions` once: [1](#0-0) 

That cached `user_role` is what subsequent requests are authorized against, e.g. via session lookup, for the entire session lifetime (`SessionTimeout` cookie idle window and up to `SessionReaperExpiration`, defaults 15m/240h respectively) — there is no re-query to the OIDC provider to confirm the user's current group membership on each request or on any interval.

The reaper for OIDC only deletes rows older than the reaper expiration threshold; it performs no re-validation of role/claims: [2](#0-1) 

Contrast this with the LDAP provider, which explicitly documents and implements continuous re-sync of cached role/session state against the upstream server: [3](#0-2) 

This is the same root-cause class as the Staking bug: a per-actor privilege value (`stakedTimeBonus` in Staking; `user_role` in `oidc_sessions`) is computed once from a mutable, admin-controlled source of truth (staking parameters; IdP group memberships) and cached without a mechanism to "poke"/recompute it when the source of truth changes. In Staking, a user demoted from a group loses their in-protocol privilege, but existing lock-ins keep the stale (possibly higher) value; here, a user who is removed from an admin/edit IdP group (e.g., offboarded, demoted, or whose group membership is revoked for security reasons) keeps their previously-cached elevated `user_role` in `oidc_sessions` and any active API tokens (`oidc_user_api_tokens`) until the session naturally expires or the reaper purges it — there is no operator action available to force immediate revalidation short of expiring the session server-side or waiting out `SessionTimeout`/`UserAPITokenDuration`.

### Impact Explanation
An authenticated OIDC user whose role is downgraded/revoked at the identity provider (e.g., removed from `NodeAdmins` claim group) retains their prior elevated Chainlink node role (Admin/Edit/Run) for as long as their session cookie or issued API token remains valid — up to `UserAPITokenDuration` (default 240h) for API tokens, or until `SessionReaperExpiration` for sessions. This is a stale/unauthorized-privilege retention bug directly analogous to the accepted Sherlock Medium finding: a permanent (bounded only by long TTLs) authorization mismatch between the source-of-truth (IdP) and the cached local decision, with no re-sync mechanism to normalize it, unlike the LDAP driver which was explicitly designed to solve this exact problem.

### Likelihood Explanation
This is reachable by any legitimate OIDC-authenticated user/session — no privileged or malicious-peer/network-layer capability is required; it is purely a gap in the WebServer OIDC authentication path used by the node's HTTP API. It manifests whenever an operator revokes/downgrades a user's IdP group membership expecting immediate effect, which is a normal, expected administrative operation.

### Recommendation
Add a periodic upstream re-validation mechanism for OIDC sessions and API tokens analogous to LDAP's `UpstreamSyncInterval`/sync job — e.g., periodically (or on each authenticated request, rate-limited) re-fetch/verify the user's current claims from the OIDC provider (via refresh token or re-introspection) and update or revoke the cached `user_role` in `oidc_sessions`/`oidc_user_api_tokens` accordingly, mirroring `core/sessions/ldapauth/sync.go`'s approach.

### Proof of Concept
1. Configure Chainlink node with `WebServer.AuthenticationMethod = 'oidc'`.
2. User logs in while a member of the IdP's `NodeAdmins` claim group; `handleTokenExchange` maps this to `Admin` role and stores it in `oidc_sessions.user_role`. [4](#0-3) 
3. Operator removes the user from `NodeAdmins` in the identity provider (intending to revoke admin access immediately).
4. The user's existing session cookie remains valid (idle timeout `SessionTimeout`, default 15m, refreshed on activity) and any issued API token remains valid up to `UserAPITokenDuration` (default 240h) — see `FindUserByAPIToken`, which only checks `created_at + duration >= now()`, never re-checking the IdP: [5](#0-4) 
5. The user continues to exercise Admin-level API access against the Chainlink node despite no longer having Admin group membership at the IdP, until the cached record naturally expires or is reaped.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L233-256)
```go
	// Map the claims to a role and insert a newly created session paired with role mapping for user
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

**File:** core/sessions/oidcauth/oidc.go (L298-326)
```go
func (oi *oidcAuthenticator) FindUserByAPIToken(ctx context.Context, apiToken string) (clsessions.User, error) {
	if !oi.config.UserAPITokenEnabled() {
		return clsessions.User{}, errors.New("API token is not enabled")
	}

	var foundUser clsessions.User
	err := sqlutil.TransactDataSource(ctx, oi.ds, nil, func(tx sqlutil.DataSource) error {
		// Query the oidc user API token table for given token, user role and email are cached so
		// no further upstream OIDC query is performed, sessions and tokens are synced against the upstream server
		// via the UpstreamSyncInterval config and reaper.go sync implementation
		var foundUserToken struct {
			UserEmail string
			UserRole  clsessions.UserRole
			Valid     bool
		}
		if err := tx.GetContext(ctx, &foundUserToken,
			"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM oidc_user_api_tokens WHERE token_key = $1",
			apiToken, oi.config.UserAPITokenDuration().Duration(),
		); err != nil {
			return err
		}
		if !foundUserToken.Valid {
			return clsessions.ErrUserSessionExpired
		}
		foundUser = clsessions.User{
			Email: foundUserToken.UserEmail,
			Role:  foundUserToken.UserRole,
		}
		return nil
```

**File:** core/sessions/oidcauth/reaper.go (L37-50)
```go
func (sr *sessionReaper) Work(ctx context.Context) {
	recordCreationStaleThreshold := sr.config.SessionReaperExpiration().Before(
		sr.config.SessionTimeout().Before(time.Now()))
	err := sr.deleteStaleSessions(ctx, recordCreationStaleThreshold)
	if err != nil {
		sr.lggr.Error("unable to reap stale sessions: ", err)
	}
}

// DeleteStaleSessions deletes all sessions before the passed time.
func (sr *sessionReaper) deleteStaleSessions(ctx context.Context, before time.Time) error {
	_, err := sr.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE created_at < $1", before)
	return err
}
```

**File:** core/sessions/ldapauth/ldap.go (L10-19)
```go
Note: user can have only one API token at a time, and token expiration is enforced

User session and roles are cached and revalidated with the upstream service at the interval defined in
the local LDAP config through the Application.sessionReaper implementation in reaper.go.

Changes to the upstream identity server will propagate through and update local tables (web sessions, API tokens)
by either removing the entries or updating the roles. This sync happens for every auth endpoint hit, and
via the defined sync interval. One goroutine is created to coordinate the sync timing in the New function

This implementation is read only; user mutation actions such as Delete are not supported.
```
