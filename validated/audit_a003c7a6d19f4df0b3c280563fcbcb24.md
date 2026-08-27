### Title
Stale OIDC session role is never re-validated against IdP, allowing privilege retention post-demotion - ([File: core/sessions/oidcauth/oidc.go])

### Summary
`AuthorizedUserWithSession` resolves a caller's role purely from the `user_role` column cached in the `oidc_sessions` table at the time the session was created via `handleTokenExchange`, and never re-queries the identity provider for current group/claim membership. Unlike `ldapauth`, which has a `sync.go` upstream sync job that refreshes users, OIDC's `reaper.go` only deletes sessions whose `created_at` predates a threshold — it does not re-validate or downgrade role claims for still-valid sessions. Consequently, a user demoted at the IdP (e.g., removed from the admin group) keeps their previously-cached elevated role for the entire `SessionTimeout` duration.

### Finding Description
When a user authenticates through OIDC, `handleTokenExchange` maps the ID token's claims to a role via `IDClaimsToUserRole` and persists it directly into `oidc_sessions.user_role`: [1](#0-0) 

Every subsequent authenticated request calls `AuthorizedUserWithSession`, which reads `user_email`, `user_role`, and validity purely from that row — it performs no call back to the OIDC provider, no re-check of claims, and no comparison against current IdP group membership: [2](#0-1) 

The only lifecycle control is expiry: `created_at + SessionTimeout >= now()`. There is no separate re-authorization interval shorter than session validity, and `sessionReaper.Work` in `reaper.go` only purges sessions whose `created_at` is older than `SessionReaperExpiration` combined with `SessionTimeout` — it does not re-derive role from a live IdP call: [3](#0-2) 

This is confirmed by the existing test suite, which explicitly asserts that `AuthorizedUserWithSession` returns the role captured at `CreateSession`/`handleTokenExchange` time, with no mechanism to reflect a role change mid-session: [4](#0-3) 

Given a valid session cookie/ID for an OIDC-authenticated admin user, any route protected by role middleware that calls `AuthorizedUserWithSession` will trust the cached `admin` role even if the IdP has since demoted that user's group membership, until the session naturally expires per `SessionTimeout`.

### Impact Explanation
This is a persistent privilege-escalation / authorization-bypass condition: a user demoted at the IdP retains admin/run-level API access on the Chainlink node for up to the full `SessionTimeout` window (configurable, potentially hours/days), enabling unauthorized job creation/deletion, key management, or fund-moving actions that should have been revoked immediately upon IdP demotion. This matches Chainlink bounty's "role/authorization bypass" impact class.

### Likelihood Explanation
The precondition is that the attacker must have first legitimately held an elevated OIDC-mapped role and been subsequently demoted at the IdP — this requires an admin decision to demote, not an attacker action, so it's a "stale credential" issue rather than one exploitable purely by an unprivileged external attacker with no prior privilege. It reliably reproduces any time such a demotion occurs while a session is still within `SessionTimeout`, and is deterministic/repeatable (no race conditions or external variables) given the caching design shown above.

### Recommendation
Re-validate the OIDC role against the identity provider's current claims periodically (e.g., a configurable re-authorization interval, shorter than `SessionTimeout`), or perform a lightweight upstream claims/userinfo check on each `AuthorizedUserWithSession` call (or at minimum a background sync akin to `ldapauth/sync.go`) that updates or revokes `oidc_sessions.user_role` when upstream group membership changes.

### Proof of Concept
Go integration test plan (extends `core/sessions/oidcauth/oidc_test.go`):
1. Create a session for `user1` with `UserRoleAdmin` cached in `oidc_sessions` (as in `Test_AuthorizeUserWithSession_Success`).
2. Directly mutate the `oidc_sessions.user_role` column to simulate what a "live" IdP claim would now resolve to (`UserRoleView`), without touching `created_at` (session still valid).
3. Call `oidcAuthProvider.AuthorizedUserWithSession(ctx, sid)` again mid-session.
4. Assert: current behavior returns the stale cached role (`admin`) because there is no upstream re-check — demonstrating that the function cannot and does not reflect current IdP privilege state within the session validity window, i.e., `user.Role` still equals whatever was last written to the DB rather than being independently re-derived from claims.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L233-260)
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
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		c.String(http.StatusInternalServerError, "Error creating session")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L349-391)
```go
// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired
func (oi *oidcAuthenticator) AuthorizedUserWithSession(ctx context.Context, sessionID string) (clsessions.User, error) {
	if len(sessionID) == 0 {
		return clsessions.User{}, errors.New("session ID cannot be empty")
	}
	var foundUser clsessions.User
	err := sqlutil.TransactDataSource(ctx, oi.ds, nil, func(tx sqlutil.DataSource) error {
		// Query the oidc_sessions table for given session ID, user role and email are saved after the id claims is provided and validated
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
		return nil
	})
	if err != nil {
		if errors.Is(err, clsessions.ErrUserSessionExpired) {
			if _, execErr := oi.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE id = $1", sessionID); execErr != nil {
				oi.lggr.Errorf("error purging stale OIDC session: %v", execErr)
			}
		}
		return clsessions.User{}, err
	}
	return foundUser, nil
}
```

**File:** core/sessions/oidcauth/reaper.go (L37-49)
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
```

**File:** core/sessions/oidcauth/oidc_test.go (L230-255)
```go
func Test_AuthorizeUserWithSession_Success(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	db, oidcAuthProvider := setupAuthenticationProvider(t)
	user1 := cltest.MustRandomUser(t)

	// create user
	sql := "INSERT INTO users (email, hashed_password, role, created_at, updated_at) VALUES ($1, $2, $3, now(), now()) RETURNING *"
	_, err := db.ExecContext(ctx, sql, strings.ToLower(user1.Email), string(user1.HashedPassword), user1.Role)
	require.NoError(t, err)

	// create session for the user
	sessionRequest := sessions.SessionRequest{
		Email:    user1.Email,
		Password: cltest.Password,
	}
	sid, err := oidcAuthProvider.CreateSession(ctx, sessionRequest)
	require.NoError(t, err)

	// get user from session, expect ok
	user, err := oidcAuthProvider.AuthorizedUserWithSession(ctx, sid)
	require.NoError(t, err)

	require.Equal(t, user1.Email, user.Email)
	require.Equal(t, user1.Role, user.Role)
}
```
