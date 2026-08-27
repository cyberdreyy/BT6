### Title
Stale cached `user_role` in `oidc_sessions` allows continued admin authorization after `UpdateRole` demotion - ([File: core/sessions/oidcauth/oidc.go])

### Summary
`AuthorizedUserWithSession` in the OIDC authenticator authorizes purely from the `user_role` column cached in the `oidc_sessions` table at session-creation time, with no join or re-check against the live `users` table role. [1](#0-0)  Unlike the local-auth implementation, which explicitly purges a user's active `sessions` rows inside `UpdateRole` when their role changes, there is no equivalent invalidation of `oidc_sessions` tied to the affected email when a role change occurs.

### Finding Description
For the local-admin-fallback login path, `CreateSession` reads the current role from the `users` table and writes it into `oidc_sessions.user_role` at login time. [2](#0-1)  From then on, every subsequent request authenticated by that session cookie is authorized solely via:

```
SELECT user_email, user_role, created_at + $2 >= now() as valid FROM oidc_sessions WHERE id = $1
``` [3](#0-2) 

This returned `user_role` is trusted directly as the caller's role for the remainder of the session lifetime (until the session's `created_at` + `SessionTimeout` expires), with no comparison to the `users.role` value. [4](#0-3) 

By contrast, `localauth.orm.UpdateRole` proactively invalidates existing sessions for the demoted user as part of the same transaction that updates the role:
```go
_, err = tx.ExecContext(ctx, "DELETE FROM sessions WHERE email = lower($1)", email)
``` [5](#0-4) 

No analogous `DELETE FROM oidc_sessions WHERE user_email = ...` statement was found tied to the demotion path (`core/web/user_controller.go`'s `UpdateRole` handler calls `AuthenticationProvider().UpdateRole`, which for the OIDC provider updates the `users` table but was not observed to purge `oidc_sessions`). [6](#0-5)  The only mechanism that clears stale `oidc_sessions` rows is the time-based reaper, which purges by `created_at` age, not by role change. [7](#0-6) 

As a result, a session created while the user held the admin role continues to present `Role: UserRoleAdmin` to `RequiresAdminRole`-gated routes even after a real admin subsequently demotes that user via the `UpdateRole` API, until the session naturally times out.

Note: I was not able to directly view the full body of the OIDC provider's `UpdateRole` method (only its presence in the interface and mock were confirmed) in the time available, so I cannot 100% confirm it never issues a corresponding `oidc_sessions` purge; this assessment is based on the observed `AuthorizedUserWithSession` cache-only read path and the absence of any `DELETE FROM oidc_sessions ... user_email` statement outside of the reaper and `DeleteUserSession`/`ClearNonCurrentSessions`.

### Impact Explanation
This is a session/role-cache invalidation gap allowing privilege retention: a demoted user (or an admin whose credentials were later restricted) can continue exercising admin-only capabilities (job/spec management, key management, other users' credentials) for the remaining lifetime of their pre-existing session, directly contradicting the intent of the demotion action. This maps to Chainlink's "authorization bypass / privilege escalation via stale session" impact class.

### Likelihood Explanation
Requires the attacker to have first held (or been granted) a legitimate admin OIDC/local-fallback session before being demoted — this is squarely inside the described precondition (previously admin, demoted afterward), and does not require any credential theft or extra vulnerability: simply replaying the still-cookie-valid session against an admin route succeeds until `SessionTimeout` elapses.

### Recommendation
In the OIDC provider's `UpdateRole` implementation (and any code path that changes a user's role in `users`), also delete/invalidate all `oidc_sessions` (and `oidc_user_api_tokens`) rows for that `user_email`, mirroring `localauth.orm.UpdateRole`'s `DELETE FROM sessions WHERE email = lower($1)`. Alternatively, have `AuthorizedUserWithSession` re-validate the cached role against the live `users.role` (for the local-fallback case) on every request instead of trusting the cached value for the full session lifetime.

### Proof of Concept
1. Create a user with role `admin` in the `users` table.
2. Call `oidcAuthProvider.CreateSession` (local-fallback path) to obtain a session ID; confirm `AuthorizedUserWithSession` returns `Role: UserRoleAdmin`.
3. Call `oidcAuthProvider.UpdateRole(ctx, email, "view")` to demote the user.
4. Re-call `oidcAuthProvider.AuthorizedUserWithSession(ctx, sameSessionID)` — assert (expected fix) it returns `Role: UserRoleView` or an error; document that current behavior returns the stale `UserRoleAdmin`.
5. At handler level, replay the old session cookie against a `RequiresAdminRole`-gated route (e.g., `/v2/users` PATCH) after step 3 and assert current code returns `200 OK` instead of the expected `403 Forbidden`.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L356-380)
```go
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
```

**File:** core/sessions/oidcauth/oidc.go (L412-434)
```go
func (oi *oidcAuthenticator) CreateSession(ctx context.Context, sr clsessions.SessionRequest) (string, error) {
	foundUser, err := oi.localLoginFallback(ctx, sr)
	if err != nil {
		return "", err
	}

	sanitizedEmail := strings.ReplaceAll(sr.Email, "\n", "")
	sanitizedEmail = strings.ReplaceAll(sanitizedEmail, "\r", "")
	oi.lggr.Infof("Successful local admin login request for user %s - %s", sanitizedEmail, foundUser.Role)

	// Save local admin session, user, and role to sessions table
	// Sessions are set to expire after the duration + creation date elapsed
	session := clsessions.NewSession()
	_, err = oi.ds.ExecContext(ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		session.ID,
		strings.ToLower(sr.Email),
		foundUser.Role,
	)
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		return "", fmt.Errorf("error creating local OIDC session: %w", err)
	}
```

**File:** core/sessions/localauth/orm.go (L280-284)
```go
		_, err = tx.ExecContext(ctx, "DELETE FROM sessions WHERE email = lower($1)", email)
		if err != nil {
			o.lggr.Errorw("Failed to purge user sessions for UpdateRole", "err", err)
			return pkgerrors.New("error updating API user")
		}
```

**File:** core/web/user_controller.go (L108-159)
```go
// UpdateRole changes role field of a specified API user.
func (u *UserController) UpdateRole(c *gin.Context) {
	ctx := c.Request.Context()
	type updateUserRequest struct {
		Email   string `json:"email"`
		NewRole string `json:"newRole"`
	}

	var request updateUserRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	// Don't allow current admin user to edit self
	sessionUser, ok := webauth.GetAuthenticatedUser(c)
	if !ok {
		jsonAPIError(c, http.StatusInternalServerError, errors.New("failed to obtain current user from context"))
		return
	}
	if strings.EqualFold(sessionUser.Email, request.Email) {
		jsonAPIError(c, http.StatusBadRequest, errors.New("can not change state or permissions of current admin user"))
		return
	}

	// In case email/role is not specified try to give friendlier/actionable error messages
	if request.Email == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("email flag is empty, must specify an email"))
		return
	}
	if request.NewRole == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("new-role flag is empty, must specify a new role, possible options are 'admin', 'edit', 'run', 'view'"))
		return
	}
	_, err := clsession.GetUserRole(request.NewRole)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("new role does not exist, possible options are 'admin', 'edit', 'run', 'view'"))
		return
	}

	user, err := u.App.AuthenticationProvider().UpdateRole(ctx, request.Email, request.NewRole)
	if err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, errors.Wrap(err, "error updating API user"))
		return
	}

	jsonAPIResponse(c, presenters.NewUserResource(user), "user")
}
```

**File:** core/sessions/oidcauth/reaper.go (L46-49)
```go
// DeleteStaleSessions deletes all sessions before the passed time.
func (sr *sessionReaper) deleteStaleSessions(ctx context.Context, before time.Time) error {
	_, err := sr.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE created_at < $1", before)
	return err
```
