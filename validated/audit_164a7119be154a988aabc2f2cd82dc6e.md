This is a genuine analog worth documenting: `AuthorizedUserWithSession` and `FindUserByAPIToken` in `core/sessions/ldapauth/ldap.go` read only the cached local `ldap_sessions`/`ldap_user_api_tokens` tables and never re-check upstream LDAP state per-request — reconciliation with the upstream source of truth happens only via the periodic `LDAPServerStateSyncer.Work` background job. This mirrors the Teller bug: an authorization-relevant state change upstream (user deactivated/role downgraded/removed from group) does not immediately propagate to the state actually consulted for authorization decisions, leaving a stale-but-still-"ACCEPTED"-like session/token valid until the next sync tick.

### Title
Stale LDAP session/API-token role authorization due to deferred upstream sync - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`ldapAuthenticator.AuthorizedUserWithSession` and `ldapAuthenticator.FindUserByAPIToken` authorize every request purely from the locally cached `ldap_sessions` / `ldap_user_api_tokens` tables, without contacting the upstream LDAP server on the request path. [1](#0-0) [2](#0-1) 

### Finding Description
Reconciliation with the upstream LDAP directory (removing revoked users, downgrading roles, purging deactivated members) is only performed by `LDAPServerStateSyncer.Work`, which runs on a background timer (`UpstreamSyncInterval`) or on startup — not synchronously as part of the authenticated request path. [3](#0-2) [4](#0-3) 

The package doc explicitly states the design intent ("This sync happens for every auth endpoint hit, and via the defined sync interval"), but the actual authorization functions (`AuthorizedUserWithSession`, `FindUserByAPIToken`) do not perform any upstream check or trigger a sync — they only validate against the locally stored `created_at + timeout >= now()` expiry, then trust the cached `user_role` value. [5](#0-4) 

This is directly analogous to the Teller `CollateralManager` bug: an authoritative state transition (loan default / user revocation-role change) happens in one place (chain default condition / upstream LDAP directory), but the state actually consulted for subsequent privileged actions (loan status / session role) is not updated at the point of use, only via a separate, out-of-band process, leaving a window where stale privileges remain honored.

### Impact Explanation
If `UpstreamSyncInterval` is non-zero (the supported "polling" mode), a user removed from an admin/edit group, deactivated, or otherwise revoked upstream retains their previously cached role/session in `ldap_sessions` and `ldap_user_api_tokens` and can continue making authenticated, privileged API/GraphQL calls (job management, key operations, etc.) until the next sync tick fires and purges/downgrades the local row. This is a role/authorization bypass window driven purely by stale cached state, not a live upstream re-validation.

### Likelihood Explanation
This occurs on every deployment configured with LDAP auth and a non-instant `UpstreamSyncInterval`, which is an explicitly documented and supported configuration path — no attacker action is required beyond continuing to use an already-issued session/token after their upstream access was revoked.

### Recommendation
Perform (or trigger) an upstream re-validation — or at minimum check a `is_active`/`revoked` flag synced more eagerly — within `AuthorizedUserWithSession` and `FindUserByAPIToken` on the request path, rather than relying solely on the background `LDAPServerStateSyncer` interval, consistent with the behavior described in the package's own documentation comment.

### Proof of Concept
1. Configure the node with `LDAP` auth and `UpstreamSyncInterval` set to a non-zero interval (e.g. 1h).
2. User logs in successfully; a row is inserted into `ldap_sessions` with cached `user_role = 'admin'`.
3. Administrator removes the user from the LDAP admin group upstream.
4. Before the next `LDAPServerStateSyncer.Work` tick, the user continues issuing requests with the existing session cookie; `AuthorizedUserWithSession` (`core/sessions/ldapauth/ldap.go:345-373`) only checks `created_at + SessionTimeout >= now()`, so the stale `admin` role is honored and privileged actions succeed despite the upstream revocation.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L10-17)
```go
Note: user can have only one API token at a time, and token expiration is enforced

User session and roles are cached and revalidated with the upstream service at the interval defined in
the local LDAP config through the Application.sessionReaper implementation in reaper.go.

Changes to the upstream identity server will propagate through and update local tables (web sessions, API tokens)
by either removing the entries or updating the roles. This sync happens for every auth endpoint hit, and
via the defined sync interval. One goroutine is created to coordinate the sync timing in the New function
```

**File:** core/sessions/ldapauth/ldap.go (L204-236)
```go
// FindUserByAPIToken retrieves a possible stored user and role from the ldap_user_api_tokens table store
func (l *ldapAuthenticator) FindUserByAPIToken(ctx context.Context, apiToken string) (sessions.User, error) {
	if !l.config.UserApiTokenEnabled() {
		return sessions.User{}, errors.New("API token is not enabled ")
	}

	// Query the ldap user API token table for given token, user role and email are cached so
	// no further upstream LDAP query is performed, sessions and tokens are synced against the upstream server
	// via the UpstreamSyncInterval config and reaper.go sync implementation
	var foundUserToken struct {
		UserEmail string
		UserRole  sessions.UserRole
		Valid     bool
	}
	err := l.ds.GetContext(ctx, &foundUserToken,
		"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_user_api_tokens WHERE token_key = $1",
		apiToken, l.config.UserAPITokenDuration().Duration(),
	)
	if err != nil {
		return sessions.User{}, err
	}
	if !foundUserToken.Valid { // API Token expired, purge
		if _, execErr := l.ds.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE token_key = $1", apiToken); execErr != nil {
			l.lggr.Errorf("error purging stale ldap API token session: %v", execErr)
		}
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	return sessions.User{
		Email: foundUserToken.UserEmail,
		Role:  foundUserToken.UserRole,
	}, nil
}
```

**File:** core/sessions/ldapauth/ldap.go (L342-373)
```go
// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired, and update session's LastUsed field. The state of the upstream LDAP server
// is polled and synced at the defined interval via a SleeperTask
func (l *ldapAuthenticator) AuthorizedUserWithSession(ctx context.Context, sessionID string) (sessions.User, error) {
	if len(sessionID) == 0 {
		return sessions.User{}, errors.New("session ID cannot be empty")
	}
	// Query the ldap_sessions table for given session ID, user role and email are cached so
	// no further upstream LDAP query is performed
	var foundSession struct {
		UserEmail string
		UserRole  sessions.UserRole
		Valid     bool
	}
	if err := l.ds.GetContext(ctx, &foundSession,
		"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_sessions WHERE id = $1",
		sessionID, l.config.SessionTimeout().Duration(),
	); err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}
	if !foundSession.Valid {
		// Sessions expired, purge
		if _, execErr := l.ds.ExecContext(ctx, "DELETE FROM ldap_sessions WHERE id = $1", sessionID); execErr != nil {
			l.lggr.Errorf("error purging stale ldap session: %v", execErr)
		}
		return sessions.User{}, sessions.ErrUserSessionExpired
	}
	return sessions.User{
		Email: foundSession.UserEmail,
		Role:  foundSession.UserRole,
	}, nil
}
```

**File:** core/sessions/ldapauth/sync.go (L93-116)
```go
func (l *LDAPServerStateSyncer) Work(ctx context.Context) {
	// Purge expired ldap_sessions and ldap_user_api_tokens
	recordCreationStaleThreshold := l.config.SessionTimeout().Before(time.Now())
	err := l.deleteStaleSessions(ctx, recordCreationStaleThreshold)
	if err != nil {
		l.lggr.Error("unable to expire local LDAP sessions: ", err)
	}
	recordCreationStaleThreshold = l.config.UserAPITokenDuration().Before(time.Now())
	err = l.deleteStaleAPITokens(ctx, recordCreationStaleThreshold)
	if err != nil {
		l.lggr.Error("unable to expire user API tokens: ", err)
	}

	// Optional rate limiting check to limit the amount of upstream LDAP server queries performed
	if !l.config.UpstreamSyncRateLimit().IsInstant() {
		if !time.Now().After(l.nextSyncTime) {
			return
		}

		// Enough time has elapsed to sync again, store the time for when next sync is allowed and begin sync
		l.nextSyncTime = time.Now().Add(l.config.UpstreamSyncRateLimit().Duration())
	}

	l.lggr.Info("Begin Upstream LDAP provider state sync after checking time against config UpstreamSyncInterval and UpstreamSyncRateLimit")
```

**File:** core/sessions/ldapauth/sync.go (L180-235)
```go
	// Remove users in the upstreamUserStateMap source of truth who are part of groups but marked as deactivated/no-active
	for i, active := range usersActiveFlags {
		if !active {
			delete(upstreamUserStateMap, dedupedEmails[i])
		}
	}

	// upstreamUserStateMap is now the most up to date source of truth
	// Now sync database sessions and roles with new data
	err = sqlutil.TransactDataSource(ctx, l.ds, nil, func(tx sqlutil.DataSource) error {
		// First, purge users present in the local ldap_sessions table but not in the upstream server
		type LDAPSession struct {
			UserEmail string
			UserRole  sessions.UserRole
		}
		var existingSessions []LDAPSession
		if err = tx.SelectContext(ctx, &existingSessions, "SELECT user_email, user_role FROM ldap_sessions WHERE localauth_user = false"); err != nil {
			return fmt.Errorf("unable to query ldap_sessions table: %w", err)
		}
		var existingAPITokens []LDAPSession
		if err = tx.SelectContext(ctx, &existingAPITokens, "SELECT user_email, user_role FROM ldap_user_api_tokens WHERE localauth_user = false"); err != nil {
			return fmt.Errorf("unable to query ldap_user_api_tokens table: %w", err)
		}

		// Create existing sessions and API tokens lookup map for later
		existingSessionsMap := make(map[string]LDAPSession)
		for _, sess := range existingSessions {
			existingSessionsMap[sess.UserEmail] = sess
		}
		existingAPITokensMap := make(map[string]LDAPSession)
		for _, sess := range existingAPITokens {
			existingAPITokensMap[sess.UserEmail] = sess
		}

		// Populate list of session emails present in the local session table but not in the upstream state
		emailsToPurge := []any{}
		for _, ldapSession := range existingSessions {
			if _, ok := upstreamUserStateMap[ldapSession.UserEmail]; !ok {
				emailsToPurge = append(emailsToPurge, ldapSession.UserEmail)
			}
		}
		// Likewise for API Tokens table
		apiTokenEmailsToPurge := []any{}
		for _, ldapSession := range existingAPITokens {
			if _, ok := upstreamUserStateMap[ldapSession.UserEmail]; !ok {
				apiTokenEmailsToPurge = append(apiTokenEmailsToPurge, ldapSession.UserEmail)
			}
		}

		// Remove any active sessions this user may have
		if len(emailsToPurge) > 0 {
			_, err = tx.ExecContext(ctx, "DELETE FROM ldap_sessions WHERE user_email = ANY($1)", pq.Array(emailsToPurge))
			if err != nil {
				return err
			}
		}
```
