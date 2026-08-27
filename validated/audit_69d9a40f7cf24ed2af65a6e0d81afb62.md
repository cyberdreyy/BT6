No vulnerability found for this question.

**Analysis supporting this conclusion:**

The premise that revoked sessions "stay valid... until a background sync runs" is incorrect for the local auth path targeted by this question.

For local auth, every authenticated `/v2` request goes through `AuthenticateBySession` → `AuthorizedUserWithSession`, which performs a **live, synchronous** database lookup of the session row via `findValidSession` (`SELECT email FROM sessions WHERE id = $1 AND last_used + $2 >= now() FOR UPDATE`) on every single request — it does not consult any cache or rely on the reaper having run.
<cite repo="EzraCole/chainlink--005" path="core/sessions/localauth/orm.go" start="68="/> [1](#0-0) 

The three revocation events named in the question each **synchronously delete the affected session row(s)** as part of handling the request itself, not by waiting for `reaper.deleteStaleSessions`:
- Logout: `SessionsController.Destroy` calls `DeleteUserSession`, which runs `DELETE FROM sessions WHERE id = $1` immediately. [2](#0-1) [3](#0-2) 
- Password change: `UserController.updateUserPassword` calls `ClearNonCurrentSessions`, which runs `DELETE FROM sessions WHERE lower(email) = lower($1) AND id != $2` immediately, before `SetPassword` is applied. [4](#0-3) [5](#0-4) 
- Role change: `UpdateRole` runs `DELETE FROM sessions WHERE email = lower($1)` inside the same transaction as the role update, immediately invalidating **all** sessions (including the admin's own) before returning. [6](#0-5) 

`reaper.deleteStaleSessions` in `core/sessions/localauth/reaper.go` only performs a separate garbage-collection pass on sessions whose `last_used` timestamp has aged past `SessionReaperExpiration`+`SessionTimeout` — it has no role in enforcing revocation from logout, password change, or role change, and does not gate any auth decision. [7](#0-6) 

Since the session row is deleted synchronously as part of the revocation request itself, and every subsequent request performs a live row check via `findValidSession`, a reused old session ID is rejected with `sessions.ErrUserSessionExpired` on the very next request — there is no window dependent on a background sync tick. [8](#0-7) 

The invariant "revocation must take effect on the next request, not on the next sync tick" is already satisfied by the existing design (direct DB delete + per-request live lookup), so the described exploit path does not hold against `deleteStaleSessions` in the local auth provider.

### Citations

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

**File:** core/sessions/localauth/orm.go (L120-124)
```go
// DeleteUserSession will delete a session by ID.
func (o *orm) DeleteUserSession(ctx context.Context, sessionID string) error {
	_, err := o.ds.ExecContext(ctx, "DELETE FROM sessions WHERE id = $1", sessionID)
	return err
}
```

**File:** core/sessions/localauth/orm.go (L243-251)
```go
// ClearNonCurrentSessions removes other sessions for the user tied to sessionID.
func (o *orm) ClearNonCurrentSessions(ctx context.Context, sessionID string) error {
	var email string
	if err := o.ds.GetContext(ctx, &email, "SELECT email FROM sessions WHERE id = $1", sessionID); err != nil {
		return err
	}
	_, err := o.ds.ExecContext(ctx, "DELETE FROM sessions WHERE lower(email) = lower($1) AND id != $2", email, sessionID)
	return err
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

**File:** core/web/sessions_controller.go (L71-89)
```go
func (sc *SessionsController) Destroy(c *gin.Context) {
	defer sc.App.WakeSessionReaper()
	ctx := c.Request.Context()

	session := sessions.Default(c)
	defer session.Clear()
	sessionID, ok := session.Get(auth.SessionIDKey).(string)
	if !ok {
		jsonAPIResponse(c, Session{Authenticated: false}, "session")
		return
	}
	if err := sc.App.AuthenticationProvider().DeleteUserSession(ctx, sessionID); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	sc.App.GetAuditLogger().Audit(audit.AuthSessionDeleted, map[string]any{"sessionID": sessionID})
	jsonAPIResponse(c, Session{Authenticated: false}, "session")
}
```

**File:** core/web/user_controller.go (L341-351)
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
```

**File:** core/sessions/localauth/reaper.go (L35-48)
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
	_, err := sr.ds.ExecContext(ctx, "DELETE FROM sessions WHERE last_used < $1", before)
	return err
}
```
