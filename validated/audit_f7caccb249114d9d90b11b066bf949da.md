Confirmed: `token_key` (API access key/secret) is stored directly on the `users` row and is only invalidated by an explicit call to `DeleteAuthToken`/overwritten by `SetAuthToken`. No other credential-rotation path clears it.

### Title
Password reset does not revoke the user's API token, allowing continued authentication via a stale credential - (File: core/web/user_controller.go)

### Summary
`UserController.UpdatePassword` treats a password change as a full credential reset for a user, but it only clears session-based access. It leaves the separately-stored API `token_key`/`token_secret` untouched, so an attacker holding a previously-issued API token retains full API access after the "compromised" password is rotated — structurally the same class of bug as Locke's `__abdicate()`, which cleared the primary `gov` credential but left the `pendingGov`/`emergency_gov` backdoor paths intact.

### Finding Description
`UserController.updateUserPassword` (called from `UpdatePassword`) only calls `ClearNonCurrentSessions` and `SetPassword`: [1](#0-0) 

It never calls `DeleteAuthToken`. The `AuthenticationProvider` interface exposes both revocation primitives as independent operations that must be invoked separately: [2](#0-1) 

The API token (`token_key`/`token_secret`) is a completely separate authentication path from the password/session cookie. `AuthenticateByToken` looks the token up directly by `token_key` and authenticates independent of the password: [3](#0-2) [4](#0-3) 

Note that `UpdateRole` correctly purges the user's *session* rows when the role changes: [5](#0-4) 
but this too does nothing to the API token — a role downgrade or account lockdown intended to restrict access leaves the old API token usable with the (new, current) role since `FindUserByAPIToken` re-reads the live row, but it does **not** address the "reset credentials because of suspected compromise" scenario, where the operator explicitly wants to invalidate all access, including the token, and only the password/session gets rotated.

This mirrors the Locke `__abdicate()` bug precisely: revoking one designated authority path (`gov` / password+session) while a second, independently-stored authority path (`pendingGov`/`emergency_gov` / API token) remains live and unaffected, enabling continued privileged access after the operator believes access has been revoked.

### Impact Explanation
If a user's password is compromised and the account owner (or an admin, via `UpdateRole`→ but specifically here `UpdatePassword`) rotates the password believing this revokes attacker access, an attacker who separately obtained the API `accessKey`/`secret` (e.g., via a leaked env var, CI secret, log, or the initial token response) retains full API-level access — including admin-level actions such as job creation, key management, and bridge/external-initiator configuration — indefinitely, since nothing else invalidates the token. This is an authentication-bypass-via-stale-credential issue with concrete impact on funds/job-run control depending on the user's role.

### Likelihood Explanation
Moderate. It requires that (a) an API token was previously issued for the account (a normal, common workflow via `NewAPIToken`), and (b) the token leaked independently of the password. This is a realistic operational scenario (CI secrets, shell history, log exposure) and the "reset password to kick out an attacker" mental model is standard incident-response practice, making the gap likely to be relied upon incorrectly by operators.

### Recommendation
Make `UpdatePassword` (and ideally any full-account lockdown flow) also invalidate the current API token, e.g., call `DeleteAuthToken` (or rotate to a fresh token) alongside `ClearNonCurrentSessions`/`SetPassword` in `updateUserPassword`. At minimum, document clearly that password rotation does not revoke API tokens, and provide/require a single "revoke all access" action that clears sessions, LDAP/OIDC session caches, and API tokens together.

### Proof of Concept
1. Create a local-auth user and call `POST /v2/users/tokens` to obtain an API `accessKey`/`secret` via `UserController.NewAPIToken`.
2. Authenticate to any protected endpoint using `AuthenticateByToken` with the issued `accessKey`/`secret` — confirm success.
3. Call `PATCH /v2/user/password` (`UserController.UpdatePassword`) to change the password, simulating an operator response to a suspected compromise.
4. Re-issue the same API request from step 2 with the original `accessKey`/`secret` — it still succeeds because `updateUserPassword` never called `DeleteAuthToken`, confirming the stale token remains a live backdoor after the "credential reset."

### Citations

**File:** core/web/user_controller.go (L341-360)
```go
func (u *UserController) updateUserPassword(c *gin.Context, user *clsession.User, newPassword string) error {
	ctx := c.Request.Context()
	sessionID, err := getCurrentSessionID(c)
	if err != nil {
		return err
	}
	orm := u.App.AuthenticationProvider()
	if err := orm.ClearNonCurrentSessions(ctx, sessionID); err != nil {
		u.App.GetLogger().Errorf("failed to clear non current user sessions: %s", err)
		return errors.New("unable to update password")
	}
	if err := orm.SetPassword(ctx, user, newPassword); err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			return errUnsupportedForAuth
		}
		u.App.GetLogger().Errorf("failed to update current user password: %s", err)
		return errors.New("unable to update password")
	}
	return nil
}
```

**File:** core/sessions/authentication.go (L52-61)
```go
	CreateSession(ctx context.Context, sr SessionRequest) (string, error)
	ClearNonCurrentSessions(ctx context.Context, sessionID string) error
	CreateUser(ctx context.Context, user *User) error
	UpdateRole(ctx context.Context, email, newRole string) (User, error)
	SetAuthToken(ctx context.Context, user *User, token *auth.Token) error
	CreateAndSetAuthToken(ctx context.Context, user *User) (*auth.Token, error)
	DeleteAuthToken(ctx context.Context, user *User) error
	SetPassword(ctx context.Context, user *User, newPassword string) error
	TestPassword(ctx context.Context, email, password string) error
	Sessions(ctx context.Context, offset, limit int) ([]Session, error)
```

**File:** core/web/auth/auth.go (L75-112)
```go
// AuthenticateByToken authenticates a User by their API token.
//
// Implements authMethod
func AuthenticateByToken(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	token := &auth.Token{
		AccessKey: c.GetHeader(APIKey),
		Secret:    c.GetHeader(APISecret),
	}
	if token.AccessKey == "" {
		return auth.ErrorAuthFailed
	}

	if token.Secret == "" {
		return auth.ErrorAuthFailed
	}

	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/sessions/localauth/orm.go (L48-53)
```go
// FindUserByAPIToken will attempt to return an API user via the user's table token_key column.
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}
```

**File:** core/sessions/localauth/orm.go (L259-296)
```go
// UpdateRole overwrites role field of the user specified by email.
func (o *orm) UpdateRole(ctx context.Context, email, newRole string) (sessions.User, error) {
	var userToEdit sessions.User

	if newRole == "" {
		return userToEdit, pkgerrors.New("user role must be specified")
	}

	err := sqlutil.TransactDataSource(ctx, o.ds, nil, func(tx sqlutil.DataSource) error {
		// First, attempt to load specified user by email
		if err := tx.GetContext(ctx, &userToEdit, "SELECT * FROM users WHERE lower(email) = lower($1)", email); err != nil {
			return pkgerrors.New("no matching user for provided email")
		}

		// Patch validated role
		userRole, err := sessions.GetUserRole(newRole)
		if err != nil {
			return err
		}
		userToEdit.Role = userRole

		_, err = tx.ExecContext(ctx, "DELETE FROM sessions WHERE email = lower($1)", email)
		if err != nil {
			o.lggr.Errorw("Failed to purge user sessions for UpdateRole", "err", err)
			return pkgerrors.New("error updating API user")
		}

		sql := "UPDATE users SET role = $1, updated_at = now() WHERE lower(email) = lower($2) RETURNING *"
		if err := tx.GetContext(ctx, &userToEdit, sql, userToEdit.Role, email); err != nil {
			o.lggr.Errorw("Error updating API user", "err", err)
			return pkgerrors.New("error updating API user")
		}

		return nil
	})

	return userToEdit, err
}
```
