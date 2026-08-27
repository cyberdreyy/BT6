### Title
Admin API Permits Deleting or Demoting the Last Remaining Admin User, Enabling Node Lockout - (File: core/web/user_controller.go)

### Summary
The Chainlink node's user-management API (`/v2/users` endpoints) implements the same multi-admin design flagged in the external report: several distinct users can hold the `admin` role, and any one of them can unilaterally delete or demote any other admin. Unlike the on-chain contract mitigation described in the report ("verification that the contract can not be bricked — always at least 1 admin"), the node's `UserController.Delete` and `UserController.UpdateRole` handlers only block a user from acting on their *own* account; they perform no check on whether the target is the last remaining admin.

### Finding Description
`UserController.UpdateRole` and `UserController.Delete` in `core/web/user_controller.go` guard only against a caller mutating their own session user: [1](#0-0) [2](#0-1) 

Neither handler queries `ListUsers`/counts admins to verify that at least one other `UserRoleAdmin` account will remain before proceeding. The underlying ORM calls are similarly unconditional: [3](#0-2) [4](#0-3) 

Because `role` is a plain per-row column with no application-level invariant (e.g., "at least one admin must remain"), any authenticated admin — or two admins racing concurrently via the CLI (`admin_commands.go` `ChangeRole`/`DeleteUser`) or the HTTP API — can remove or demote every other admin account, including scenarios where:
1. Admin A deletes Admin B, then Admin B (before its session/token is invalidated) deletes Admin A, or several admins concurrently delete each other, converging on a state with zero admin users.
2. A single malicious or compromised admin sequentially deletes/demotes all peer admins to become sole admin, exactly as described in the external report's point 1 ("one admin address is compromised, it can become the only admin by removing all other admins").

This is the same "concurrent admin action, no majority/quorum requirement" root cause the report identifies for `AccessProtected.sol`/`TokenVesting.sol`, but reachable through the node's authenticated user/role management surface rather than a smart contract.

### Impact Explanation
If the last admin account is deleted or demoted, the node becomes locked out of admin-level API management (creating/editing/deleting users, changing roles) with no in-band recovery path through the web/API layer — recovery would require direct database access or CLI on the node host. This is a legitimate node-lockout/denial-of-admin-access condition, distinct from a purely cosmetic issue, and mirrors the "contract could be bricked" risk the client explicitly mitigated on-chain but does not appear to be mitigated in the node's own user-role system.

### Likelihood Explanation
Requires at least one authenticated admin actor (a compromised admin credential, a rogue admin, or two admins racing/conflicting through concurrent CLI/API calls). It does not require any unauthenticated access. Given that admin credentials are a normal operational attack target (phished/leaked API tokens, disgruntled operators), and that no server-side safeguard exists, likelihood is moderate — bounded by the need for admin-level access, but the exploit itself is trivial once that access is obtained (single API/CLI call per victim admin).

### Recommendation
Add a server-side invariant in `UserController.Delete` and `UserController.UpdateRole` (and their `AuthenticationProvider`/`orm` implementations) that rejects the operation if it would remove the last user with `UserRoleAdmin`, similar to the "always at least 1 admin" check the report notes was implemented on-chain. Consider performing this check inside the same DB transaction as the delete/update (as already used in `UpdateRole`'s `sqlutil.TransactDataSource`) with a `SELECT ... FOR UPDATE` or `COUNT(*)` guard to close the race-condition window between concurrent admin requests.

### Proof of Concept
1. Node configured with local auth and two admin users, `admin1@example.com` and `admin2@example.com`.
2. Authenticate as `admin1`, call `DELETE /v2/users/admin2%40example.com` — succeeds (`UserController.Delete`, no last-admin check) [5](#0-4) .
3. Authenticate as `admin2` concurrently (before session purge takes effect) and call `DELETE /v2/users/admin1%40example.com`, or alternatively have `admin1` call `PATCH /v2/users` to demote itself is blocked, but nothing stops `admin1` from demoting/deleting every other admin in sequence via repeated `DELETE`/`PATCH` calls, eventually leaving zero or one hostile admin account — no code path in `user_controller.go` or `localauth/orm.go` prevents this end state.

### Citations

**File:** core/web/user_controller.go (L122-131)
```go
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
```

**File:** core/web/user_controller.go (L161-198)
```go
// Delete deletes an API user and any sessions by email
func (u *UserController) Delete(c *gin.Context) {
	ctx := c.Request.Context()
	email := c.Param("email")

	// Attempt find user by email
	user, err := u.App.AuthenticationProvider().FindUser(ctx, email)
	if err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		jsonAPIError(c, http.StatusBadRequest, errors.Errorf("specified user not found: %s", email))
		return
	}

	// Don't allow current admin user to delete self
	sessionUser, ok := webauth.GetAuthenticatedUser(c)
	if !ok {
		jsonAPIError(c, http.StatusInternalServerError, errors.New("failed to obtain current user from context"))
		return
	}
	if strings.EqualFold(sessionUser.Email, email) {
		jsonAPIError(c, http.StatusBadRequest, errors.New("can not delete currently logged in admin user"))
		return
	}

	if err = u.App.AuthenticationProvider().DeleteUser(ctx, email); err != nil {
		if errors.Is(err, clsession.ErrNotSupported) {
			jsonAPIError(c, http.StatusBadRequest, errUnsupportedForAuth)
			return
		}
		u.App.GetLogger().Errorw("Error deleting API user", "err", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("error deleting API user"))
		return
	}

	jsonAPIResponse(c, presenters.NewUserResource(user), "user")
```

**File:** core/sessions/localauth/orm.go (L109-118)
```go
// DeleteUser will delete an API User and sessions by email.
func (o *orm) DeleteUser(ctx context.Context, email string) error {
	return sqlutil.TransactDataSource(ctx, o.ds, nil, func(tx sqlutil.DataSource) error {
		// session table rows are deleted on cascade through the user email constraint
		if _, err := tx.ExecContext(ctx, "DELETE FROM users WHERE email = $1", email); err != nil {
			return err
		}
		return nil
	})
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
