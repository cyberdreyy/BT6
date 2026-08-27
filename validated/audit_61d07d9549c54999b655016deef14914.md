### Title
Stale cached role in LDAP/OIDC session tables allows continued admin access after role downgrade - ([File: core/sessions/oidcauth/oidc.go -> AuthorizedUserWithSession], [File: core/sessions/ldapauth/ldap.go -> AuthorizedUserWithSession])

### Summary
When the node is configured with `LDAPAuth` or `OIDCAuth`, `AuthorizedUserWithSession` never re-checks the authoritative role source (the `users` table / upstream directory) on each request — it simply reads the `user_role` value that was cached into `ldap_sessions`/`oidc_sessions` at session-creation time. `AuthenticateBySession` (`core/web/auth/auth.go:55-71`) trusts whatever role is returned, so a demoted admin's existing session cookie continues to authorize as admin until the cache is refreshed or the session expires.

### Finding Description
`AuthenticateBySession` calls `Authenticator.AuthorizedUserWithSession(ctx, sessionID)` and sets the returned `clsessions.User` (including `Role`) directly on the gin context, which is then checked by `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` (`core/web/auth/auth.go:63-68, 200-255`).

For the `localauth` provider, this is safe: `orm.AuthorizedUserWithSession` (`core/sessions/localauth/orm.go:87-107`) validates the session and then calls `o.findUser(ctx, email)` — a live lookup against the `users` table — so a role change made via `UpdateRole` takes effect on the very next request.

For `ldapauth` and `oidcauth`, this live re-check does not happen:
- `ldapauth.AuthorizedUserWithSession` (`core/sessions/ldapauth/ldap.go:345-373`) only queries `ldap_sessions` for the cached `user_role` and a time-based `valid` flag — it never joins against current directory/group state.
- `oidcauth.AuthorizedUserWithSession` (`core/sessions/oidcauth/oidc.go:351-391`) does the same against `oidc_sessions`.

Role corrections only happen out-of-band:
- LDAP has `LDAPServerStateSyncer.Work` (`core/sessions/ldapauth/sync.go:93-284`), a periodic background task (interval controlled by `UpstreamSyncInterval`/`UpstreamSyncRateLimit`) that re-queries the LDAP directory and updates `ldap_sessions.user_role` for existing sessions. Until that sync runs, a demoted user's cached session role stays stale.
- OIDC's `sessionReaper.Work` (`core/sessions/oidcauth/reaper.go:37-50`) only deletes sessions older than a staleness threshold; it contains no logic to re-sync or invalidate `oidc_sessions.user_role` when a user's role changes (e.g. via `UpdateRole` on the local-admin fallback path). No other code path was found that updates `oidc_sessions` rows after creation.

Because `AuthenticateBySession`/`AuthenticateGQL` treat the cached role as authoritative and `RequiresAdminRole` compares only `user.Role` from that cached value, a session created while the account was `admin` continues to pass `RequiresAdminRole` after the account is demoted, for as long as the cache remains stale (bounded by `SessionTimeout` for OIDC, and by `UpstreamSyncInterval` for LDAP).

### Impact Explanation
This is a role/authorization-bypass / privilege-persistence issue: an account that has been demoted from `admin` to `view`/`edit`/`run` by a legitimate administrator can continue issuing admin-only requests (job management, key/config changes, user management) using its old session cookie, for the duration of the stale-cache window. This maps to Chainlink's "broken access control / privilege escalation" bounty impact class since it defeats the intended effect of an administrative demotion.

### Likelihood Explanation
Requires the node to be configured with `LDAPAuth` or `OIDCAuth` (not the default `local` provider, which is unaffected because it performs a live DB lookup). Given that precondition, the exploit requires no special attacker skill: the demoted user simply keeps reusing their already-issued, unexpired session cookie — no request forgery or additional credentials needed. It is fully reproducible and deterministic within the stale window (OIDC: until `SessionTimeout` elapses or the session is explicitly deleted; LDAP: until `UpstreamSyncInterval` next fires).

### Recommendation
- For `oidcauth` and `ldapauth`, invalidate or actively refresh cached session/API-token role rows whenever `UpdateRole`/`DeleteUser`/role-affecting admin actions occur (e.g. call `ClearNonCurrentSessions`/`DeleteUserSession` or update `oidc_sessions`/`ldap_sessions.user_role` in the same transaction as the role change), similar to what `LDAPServerStateSyncer` already does, but triggered synchronously on demotion rather than only on a timer.
- Alternatively, have `AuthorizedUserWithSession` for OIDC/LDAP re-validate the cached role against the current `users` table (for local/admin fallback users) on every request, mirroring `localauth.orm.AuthorizedUserWithSession`.
- Reduce `SessionTimeout`/`UpstreamSyncInterval` defaults, and document the exposure window for operators who rely on LDAP/OIDC role revocation for incident response.

### Proof of Concept
Handler/integration test plan (mirrors `core/sessions/oidcauth/oidc_test.go` patterns):
1. `setupAuthenticationProvider(t)` to get an OIDC-backed `AuthenticationProvider`.
2. Insert a user row with `role = 'admin'`.
3. Call `CreateSession` with valid credentials to obtain `sessionID`; assert `AuthorizedUserWithSession(ctx, sessionID)` returns `Role == UserRoleAdmin`.
4. Simulate demotion: execute `UPDATE users SET role = 'view' WHERE email = $1` (equivalent to what `UpdateRole`/the user_controller admin API does).
5. Reuse the same `sessionID` and call `AuthorizedUserWithSession(ctx, sessionID)` again (or send an HTTP request through `Authenticate(store, AuthenticateBySession)` + `RequiresAdminRole` wrapped handler with the original session cookie).
6. Expected (per invariant): result role should be `UserRoleView` and the admin-only handler should return `403 Forbidden`.
7. Actual (bug): `AuthorizedUserWithSession` still returns `Role == UserRoleAdmin` (read from `oidc_sessions`/`ldap_sessions` cache), and the admin-only handler succeeds — demonstrating privilege persistence past the downgrade. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/web/auth/auth.go (L55-71)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/web/auth/auth.go (L238-255)
```go
// RequiresAdminRole extracts the user object from the context, and asserts the user's role is 'admin'
func RequiresAdminRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role != clsessions.UserRoleAdmin {
			c.Abort()
			addForbiddenErrorHeaders(c, "admin", string(user.Role), user.Email)
			jsonAPIError(c, http.StatusForbidden, errors.New("Forbidden"))
			return
		}
		handler(c)
	}
}
```

**File:** core/sessions/localauth/orm.go (L87-107)
```go
func (o *orm) AuthorizedUserWithSession(ctx context.Context, sessionID string) (user sessions.User, err error) {
	if len(sessionID) == 0 {
		return sessions.User{}, sessions.ErrEmptySessionID
	}

	email, err := o.findValidSession(ctx, sessionID)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	user, err = o.findUser(ctx, email)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	if err := o.updateSessionLastUsed(ctx, sessionID); err != nil {
		return sessions.User{}, err
	}

	return user, nil
}
```

**File:** core/sessions/ldapauth/ldap.go (L345-373)
```go
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

**File:** core/sessions/ldapauth/sync.go (L93-284)
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

	// For each defined role/group, query for the list of group members to gather the full list of possible users
	users := []sessions.User{}

	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		l.lggr.Error("Failed to Dial LDAP Server: ", err)
		return
	}
	// Root level root user auth with credentials provided from config
	bindStr := l.config.BaseUserAttr() + "=" + l.config.ReadOnlyUserLogin() + "," + l.config.BaseDN()
	if err = conn.Bind(bindStr, l.config.ReadOnlyUserPass()); err != nil {
		l.lggr.Error("Unable to login as initial root LDAP user: ", err)
	}
	defer conn.Close()

	// Query for list of uniqueMember IDs present in Admin group
	adminUsers, err := l.ldapGroupMembersListToUser(conn, l.config.AdminUserGroupCN(), sessions.UserRoleAdmin)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	editUsers, err := l.ldapGroupMembersListToUser(conn, l.config.EditUserGroupCN(), sessions.UserRoleEdit)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	runUsers, err := l.ldapGroupMembersListToUser(conn, l.config.RunUserGroupCN(), sessions.UserRoleRun)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	readUsers, err := l.ldapGroupMembersListToUser(conn, l.config.ReadUserGroupCN(), sessions.UserRoleView)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}

	users = append(users, adminUsers...)
	users = append(users, editUsers...)
	users = append(users, runUsers...)
	users = append(users, readUsers...)

	// Dedupe preserving order of highest role (sorted)
	// Preserve members as a map for future lookup
	upstreamUserStateMap := make(map[string]sessions.User)
	dedupedEmails := []string{}
	for _, user := range users {
		if _, ok := upstreamUserStateMap[user.Email]; !ok {
			upstreamUserStateMap[user.Email] = user
			dedupedEmails = append(dedupedEmails, user.Email)
		}
	}

	// For each unique user in list of active sessions, check for 'Is Active' property if defined in the config. Some LDAP providers
	// list group members that are no longer marked as active
	usersActiveFlags, err := l.validateUsersActive(dedupedEmails, conn)
	if err != nil {
		l.lggr.Error("Error validating supplied user list: ", err)
	}
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

		// Remove any active API tokens this user may have
		if len(apiTokenEmailsToPurge) > 0 {
			_, err = tx.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE user_email = ANY($1)", pq.Array(apiTokenEmailsToPurge))
			if err != nil {
				return err
			}
		}

		// For each user session row, update role to match state of user map from upstream source
		var queryWhenClause strings.Builder
		emailValues := []any{}
		// Prepare CASE WHEN query statement with parameterized argument $n placeholders and matching role based on index
		for email, user := range upstreamUserStateMap {
			// Only build on SET CASE statement per local session and API token role, not for each upstream user value
			_, sessionOk := existingSessionsMap[email]
			_, tokenOk := existingAPITokensMap[email]
			if !sessionOk && !tokenOk {
				continue
			}
			emailValues = append(emailValues, email)
			fmt.Fprintf(&queryWhenClause, "WHEN user_email = $%d THEN '%s' ", len(emailValues), user.Role)
		}

		// If there are remaining user entries to update
		if len(emailValues) != 0 {
			// Set new role state for all rows in single Exec
			query := fmt.Sprintf("UPDATE ldap_sessions SET user_role = CASE %s ELSE user_role END", &queryWhenClause)
			_, err = tx.ExecContext(ctx, query, emailValues...)
			if err != nil {
				return err
			}

			// Update role of API tokens as well
			query = fmt.Sprintf("UPDATE ldap_user_api_tokens SET user_role = CASE %s ELSE user_role END", &queryWhenClause)
			_, err = tx.ExecContext(ctx, query, emailValues...)
			if err != nil {
				return err
			}
		}

		l.lggr.Info("local ldap_sessions and ldap_user_api_tokens table successfully synced with upstream LDAP state")
		return nil
	})
	if err != nil {
		l.lggr.Error("Error syncing local database state: ", err)
	}
	l.lggr.Info("Upstream LDAP sync complete")
}
```
