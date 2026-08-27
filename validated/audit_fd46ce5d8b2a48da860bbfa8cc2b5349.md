### Title
Non-atomic session validation (`findValidSession` → `findUser` → `updateSessionLastUsed`) allows a demoted user to complete requests with the stale pre-demotion role during a concurrent `UpdateRole` transaction - ([File: core/sessions/localauth/orm.go])

### Summary
`AuthorizedUserWithSession` performs three separate, unbatched database round trips instead of one atomic transaction, while `UpdateRole` demotes a user and purges sessions inside a single transaction. Because the three reads/writes in `AuthorizedUserWithSession` are not wrapped together with `UpdateRole`'s transaction boundary, a request authenticating with a soon-to-be-revoked session can read the user's pre-demotion (higher) role if it executes its `findUser` lookup before `UpdateRole` commits, even though the session row has already been (or is concurrently being) deleted.

### Finding Description
`AuthorizedUserWithSession` in `core/sessions/localauth/orm.go` executes three independent, non-transactional statements against `o.ds`: [1](#0-0) 

1. `findValidSession` — `SELECT email FROM sessions WHERE id = $1 ... FOR UPDATE` [2](#0-1) 
2. `findUser` — `SELECT * FROM users WHERE lower(email) = lower($1)` [3](#0-2) 
3. `updateSessionLastUsed` — `UPDATE sessions SET last_used = now() WHERE id = $1` [4](#0-3) 

Each call runs as its own implicit autocommit transaction (`o.ds` is not wrapped in `sqlutil.TransactDataSource`), so the `FOR UPDATE` row lock acquired in step 1 is released as soon as that single statement's own transaction commits — it provides no isolation guarantee across steps 2 and 3.

Meanwhile, `UpdateRole` performs its role change and session purge atomically in one transaction: [5](#0-4) 

Race window: if an attacker's `AuthorizedUserWithSession` call executes step 1 (`findValidSession`) *before* the admin's `UpdateRole` transaction begins its `DELETE FROM sessions`, it succeeds and releases its lock. If the attacker's step 2 (`findUser`) then executes *before* `UpdateRole`'s transaction commits (Postgres default `READ COMMITTED` isolation means uncommitted changes from `UpdateRole` are invisible to the concurrent `findUser` read), `findUser` returns the user row with the **old, higher role**. `UpdateRole` then commits, deleting the session row and applying the new role. The attacker's step 3 (`updateSessionLastUsed`) subsequently executes an `UPDATE` against the now-deleted session row; a Postgres `UPDATE` matching zero rows does not return an error, so this call silently succeeds. `AuthorizedUserWithSession` therefore returns without error, handing back a `sessions.User` populated with the stale (pre-demotion) role to `AuthenticateBySession`/`AuthenticateGQL`, which sets it on the request context for use by `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` and GraphQL resolvers: [6](#0-5) [7](#0-6) 

The downstream authorization checks (`RequiresAdminRole`, `RequiresEditRole`, resolver-side `authenticateUserIsAdmin`, etc.) trust the `Role` field on the `sessions.User` object returned by this call and cannot detect that it was read from a transaction window that has since been superseded by a committed demotion.

### Impact Explanation
This is a temporal authorization-bypass / role downgrade race: a user (or process holding that user's cookie) who is being demoted from `admin`/`edit` to a lower role can, within the narrow window between `UpdateRole`'s `DELETE FROM sessions` and its commit of the `UPDATE users SET role = ...`, complete one privileged request (e.g. an admin-only action) using the stale role, even though the intended state after the admin's action is that the account no longer has that privilege. This matches Chainlink's "role/authorization bypass" bounty impact class, since it violates the invariant that a role change (and associated session purge) must atomically and immediately revoke prior privilege.

### Likelihood Explanation
The race requires precise timing: the attacker (holder of the about-to-be-demoted session) must fire authenticated requests concurrently with the exact moment an admin calls `UpdateRole` for that account, and the `findUser` read must land inside the sub-millisecond-to-millisecond window between `UpdateRole`'s `DELETE` and its `COMMIT`. This is feasible for a determined attacker who can flood requests continuously while anticipating (or actively provoking, e.g. via slow/paced requests) a demotion, but it is not trivially reliable — it depends on database scheduling and network timing, and only single/several requests can slip through per demotion event, not a wide window. No special credentials beyond the attacker's own existing session are required, satisfying the "unprivileged/self-only-credential" attacker model, but exploitation is opportunistic rather than deterministic.

### Recommendation
Wrap `findValidSession`, `findUser`, and `updateSessionLastUsed` in a single transaction (e.g. via `sqlutil.TransactDataSource`) so the session lookup, user role lookup, and last-used update all observe a consistent, serialized view of the user and session state relative to `UpdateRole`'s transaction. Alternatively, have `AuthorizedUserWithSession` join the user and session rows in one query executed with the same lock as `findValidSession`'s `FOR UPDATE`, so that a concurrently in-flight `UpdateRole` transaction either blocks the read until it commits or the read observes the already-deleted session row and fails closed.

### Proof of Concept
1. Add a Go integration test in `core/sessions/localauth/orm_test.go` using a real (or dockerized) Postgres test database with two goroutines:
   - Goroutine A: calls `orm.UpdateRole(ctx, email, "view")` for a user currently `admin`.
   - Goroutine B: repeatedly calls `orm.AuthorizedUserWithSession(ctx, sessionID)` for that user's existing session in a tight loop, started slightly before goroutine A, using `sync.WaitGroup`/barriers to maximize the chance of overlapping with A's transaction window.
2. Instrument `UpdateRole`'s transaction (or use `pg_sleep()` injected via a test hook between the `DELETE FROM sessions` and `UPDATE users` statements) to widen the race window deterministically for the test.
3. Assert that no call to `AuthorizedUserWithSession` in goroutine B ever returns `sessions.User{Role: UserRoleAdmin}` after `UpdateRole` has started executing — i.e., every successful return either occurs strictly before `UpdateRole`'s transaction begins, or is rejected with `sessions.ErrUserSessionExpired` once the session is deleted.
4. Failing test (in the current implementation) demonstrates at least one call returning `Role: UserRoleAdmin` successfully with no error, after `UpdateRole`'s transaction has started (as evidenced by the session row already being deleted for subsequent lookups).

### Citations

**File:** core/sessions/localauth/orm.go (L55-59)
```go
func (o *orm) findUser(ctx context.Context, email string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE lower(email) = lower($1)"
	err = o.ds.GetContext(ctx, &user, sql, email)
	return
}
```

**File:** core/sessions/localauth/orm.go (L69-75)
```go
func (o *orm) findValidSession(ctx context.Context, sessionID string) (email string, err error) {
	if err := o.ds.GetContext(ctx, &email, "SELECT email FROM sessions WHERE id = $1 AND last_used + $2 >= now() FOR UPDATE", sessionID, o.sessionDuration); err != nil {
		o.lggr.Infof("query result: %v", email)
		return email, pkgerrors.Wrap(err, "no matching user for provided session token")
	}
	return email, nil
}
```

**File:** core/sessions/localauth/orm.go (L78-81)
```go
func (o *orm) updateSessionLastUsed(ctx context.Context, sessionID string) error {
	_, err := o.ds.ExecContext(ctx, "UPDATE sessions SET last_used = now() WHERE id = $1", sessionID)
	return err
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

**File:** core/sessions/localauth/orm.go (L260-296)
```go
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
