This confirms the root-cause pattern: both `ldap_sessions`/`ldap_user_api_tokens` and `oidc_sessions`/`oidc_user_api_tokens` validity are computed live at query time as `created_at + <current config duration> >= now()` rather than being fixed at creation time. The `deleteStaleSessions`/`deleteStaleAPITokens` reaper in `core/sessions/ldapauth/sync.go` also purges based on the *current* config value (`l.config.SessionTimeout().Before(time.Now())`, `l.config.UserAPITokenDuration().Before(time.Now())`), so a still-present but already-elapsed row is never guaranteed to be purged before an operator changes the duration setting.

This is directly analogous to the reported `_gracePeriod` bug class: a validity/expiry window is read from a **mutable, currently-effective configuration value** at authorization time instead of being captured as an immutable parameter of the object (the session/token) when it was created.

### Title
Session and API token expiration is recomputed from live config instead of a fixed value stored at creation, allowing previously-expired sessions/tokens to become valid again after a config change - ([File: core/sessions/ldapauth/ldap.go], [File: core/sessions/oidcauth/oidc.go])

### Summary
`AuthorizedUserWithSession` and `FindUserByAPIToken` in both the LDAP and OIDC authentication providers determine whether a session/token is still valid by comparing `created_at + <current SessionTimeout/UserAPITokenDuration>` against `now()`, where the duration is read from the live, currently-configured value rather than being fixed to the value in effect when the session/token was created.

### Finding Description
In `core/sessions/ldapauth/ldap.go`, `AuthorizedUserWithSession` runs: [1](#0-0) 
and `FindUserByAPIToken` runs the analogous query with `l.config.UserAPITokenDuration().Duration()`: [2](#0-1) 

The OIDC provider has the identical pattern in `FindUserByAPIToken`: [3](#0-2) 

In both cases, the "grace period" (`SessionTimeout` / `UserAPITokenDuration`) is **not stored as an attribute of the session/token row at creation time**; it is only stored as `created_at`, and the effective expiry is recomputed every time using whatever the currently configured duration is (via `l.config.SessionTimeout()` / `l.config.UserAPITokenDuration()`). The background reaper (`LDAPServerStateSyncer.Work` / `deleteStaleSessions` / `deleteStaleAPITokens`) has the same flaw — it purges rows using `l.config.SessionTimeout().Before(time.Now())`, the *current* config value: [4](#0-3) 

This exactly mirrors the reported bug class: a time-window parameter that should be fixed per-object at creation is instead read from mutable global state at validation time, so changing that global state retroactively changes the validity of already-created objects — including ones that had already become invalid under the old value.

### Impact Explanation
If an operator increases `SessionTimeout` (LDAP/OIDC) or `UserAPITokenDuration`, any session or API token row that already exceeded the *old* duration — but was not proactively purged (purge only happens opportunistically on the next validation attempt, or via the reaper using the config value at reaper-run time) — becomes valid again. An attacker/former user holding an old, previously-expired session cookie or API token could regain authenticated API access after such a config change, without re-authenticating. Since these sessions/tokens map directly to RBAC roles (Admin/Edit/Run/View), this can lead to unauthorized access to the node's HTTP API using stale, revoked-by-time credentials.

### Likelihood Explanation
Requires an operator to increase the timeout/duration configuration value at some point (a plausible, ordinary operational action — e.g., raising `SessionTimeout` or `UserAPITokenDuration` for convenience) while old expired-but-unpurged rows still exist in the `ldap_sessions`/`ldap_user_api_tokens`/`oidc_sessions`/`oidc_user_api_tokens` tables. Because purging is lazy (on-access) and reaper-driven (also using live config), retention of stale rows past their original expiry is likely in normal operation, making this reachable by any attacker/user who retained an old session cookie or API token.

### Recommendation
Store the computed absolute expiration timestamp (`expires_at`) as a column on the session/API token row at creation time, and compare against that fixed value rather than recomputing `created_at + config.Duration()` at validation time. This ensures that changes to `SessionTimeout`/`UserAPITokenDuration` only affect newly created sessions/tokens, consistent with the recommendation to "save the parameter as part of the action" rather than reading it live from mutable configuration.

### Proof of Concept
1. Enable LDAP (or OIDC) auth with `UserAPITokenDuration = '1h'`.
2. User creates an API token; do not use it again for over 1 hour (row remains in `ldap_user_api_tokens`/`oidc_user_api_tokens` because it's only purged lazily on validation attempt or reaper run).
3. Operator changes config to `UserAPITokenDuration = '720h'` (30 days) and restarts/reloads the node.
4. Attacker/user replays the old (previously expired, never-purged) API token against the node's authenticated API.
5. `FindUserByAPIToken` recomputes `created_at + 720h >= now()` → `true`, authentication succeeds despite the token having already exceeded the duration in effect when it was minted. [5](#0-4) [6](#0-5)

### Citations

**File:** core/sessions/ldapauth/ldap.go (L204-230)
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
```

**File:** core/sessions/ldapauth/ldap.go (L356-361)
```go
	if err := l.ds.GetContext(ctx, &foundSession,
		"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_sessions WHERE id = $1",
		sessionID, l.config.SessionTimeout().Duration(),
	); err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}
```

**File:** core/sessions/oidcauth/oidc.go (L297-337)
```go
// FindUserByAPIToken retrieves a possible stored user and role from the oidc_user_api_tokens table store
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
	})
	if err != nil {
		if errors.Is(err, clsessions.ErrUserSessionExpired) {
			// API Token expired, purge
			if _, execErr := oi.ds.ExecContext(ctx, "DELETE FROM oidc_user_api_tokens WHERE token_key = $1", apiToken); execErr != nil {
				oi.lggr.Errorf("error purging stale oidc API token session: %v", execErr)
			}
		}
		return clsessions.User{}, err
	}
	return foundUser, nil
```

**File:** core/sessions/ldapauth/sync.go (L93-104)
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
```
