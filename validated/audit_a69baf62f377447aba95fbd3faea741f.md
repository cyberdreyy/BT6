### Title
Zero-value `UserRole` bypasses `authenticateUserCanEdit`/`CanRun` deny-list checks - ([File: core/web/resolver/auth.go])

### Summary
`authenticateUserCanRun` and `authenticateUserCanEdit` implement authorization as a **deny-list** (equality/switch against `UserRoleView`/`UserRoleRun`) rather than a strict "at least X" hierarchy check. A `clsessions.User` whose `Role` field is the Go zero value (`""`, empty string) does not match any of the named constants (`admin`,`edit`,`run`,`view`), so it falls through both checks and is granted run and edit access, while only `authenticateUserIsAdmin` correctly rejects it.

### Finding Description
`sessions.UserRole` is a plain `string` type with only four named constants defined in [1](#0-0) . The `sessions.User` struct's `Role` field has no default and no non-empty invariant enforced at the type level [2](#0-1) .

The GraphQL role-check functions do not verify the role against the full set of valid values; they instead deny specific known values:

```go
func authenticateUserCanRun(ctx context.Context) error {
	...
	if session.User.Role == sessions.UserRoleView {
		return RoleNotPermittedError{session.User.Role}
	}
	return nil
}

func authenticateUserCanEdit(ctx context.Context) error {
	...
	switch session.User.Role {
	case sessions.UserRoleView, sessions.UserRoleRun:
		return RoleNotPermittedError{session.User.Role}
	default:
	}
	return nil
}
``` [3](#0-2) 

If `session.User.Role == ""`:
- `authenticateUserCanRun`: `"" == "view"` is false → returns `nil` (run access granted).
- `authenticateUserCanEdit`: `""` matches neither `case` → falls to `default` → returns `nil` (edit access granted).
- Only `authenticateUserIsAdmin` correctly rejects it, since it does `!= UserRoleAdmin` [4](#0-3) .

The trust boundary that populates `session.User` is `AuthenticateGQL`, which calls `authenticator.AuthorizedUserWithSession` and injects whatever `User` struct is returned directly into the GraphQL context with no validation that `Role` is one of the four valid constants: [5](#0-4) 

The `AuthenticationProvider` interface has multiple implementations (`localauth`, `ldapauth`, `oidcauth`) that can populate `User.Role`. In `localauth/orm.go`, `CreateUser`/`UpdateRole` route role assignment through `sessions.GetUserRole`, which rejects unrecognized/empty strings [6](#0-5) , so the local-auth creation path resists an empty role. However, this validation is not enforced at the `authenticateUser*` call sites themselves — nothing in `auth.go` re-validates `Role` against the known set before deciding access, so any code path (present or future) in `AuthorizedUserWithSession`, LDAP group-role mapping, or OIDC claim-role mapping that returns a `User{}`/partially-populated struct with a zero-value `Role` will silently grant edit/run access instead of being denied. This is a design flaw: the deny-list pattern assumes `Role` is always well-formed, which is not guaranteed by the type system or verified at the point where the GraphQL session is authenticated.

### Impact Explanation
Any credential holder whose associated `User.Role` ends up as the zero value (e.g., through an LDAP/OIDC group that isn't mapped to a role, or any future `AuthenticationProvider` bug that returns a partially-populated `User`) is incorrectly granted `run` and `edit` level GraphQL mutation access instead of being denied. This matches the "role/authorization bypass" bounty impact class — an authenticated-but-unprivileged identity escalates to edit-capable mutations (e.g., job/bridge/spec mutations gated by `authenticateUserCanEdit`).

### Likelihood Explanation
Exploitability requires a precondition: a `User` reaching `auth.go`'s checks with `Role == ""`. The `localauth` ORM's user creation/role-update paths validate roles via `GetUserRole` and reject empty/invalid strings, making this path resistant under local auth. Whether the `ldapauth`/`oidcauth` providers can produce a `User` with an unmapped/empty role for authenticated-but-unassigned directory users was not fully confirmed in this review. Regardless of the exact provider precondition, the logic flaw in `auth.go` itself is deterministic and independently reproducible via a unit test that directly injects a `GQLSession` with `Role: ""`, as the question's own PoC proposes — this does not depend on any DB/LDAP misconfiguration to demonstrate the code-level bug.

### Recommendation
Replace the deny-list/equality checks with an explicit allow-list or role-ranking model, and reject any role value that isn't one of the four known constants by default (fail closed):
```go
func authenticateUserCanEdit(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	switch session.User.Role {
	case sessions.UserRoleEdit, sessions.UserRoleAdmin:
		return nil
	default:
		return RoleNotPermittedError{session.User.Role}
	}
}
```
Apply the same allow-list pattern to `authenticateUserCanRun`. Additionally, validate `Role` against `sessions.GetUserRole` immediately after `AuthorizedUserWithSession` in `AuthenticateGQL` and refuse to set the GQL session if the role is invalid/empty.

### Proof of Concept
Go table test in `core/web/resolver/auth_test.go` (new or extended):
```go
func TestAuthenticateUserCanEdit_ZeroValueRole(t *testing.T) {
	ctx := auth.WithGQLAuthenticatedSession(context.Background(), sessions.User{Role: sessions.UserRole("")}, "sess-id")
	err := authenticateUserCanEdit(ctx)
	require.Error(t, err) // currently fails: err is nil, expected RoleNotPermittedError
	var roleErr RoleNotPermittedError
	require.ErrorAs(t, err, &roleErr)
}

func TestAuthenticateUserCanRun_ZeroValueRole(t *testing.T) {
	ctx := auth.WithGQLAuthenticatedSession(context.Background(), sessions.User{Role: sessions.UserRole("")}, "sess-id")
	err := authenticateUserCanRun(ctx)
	require.Error(t, err) // currently fails: err is nil
}
```
Expected current (buggy) behavior: both assertions fail because `err` is `nil`, confirming edit/run access is incorrectly granted to a zero-value-role session.

### Citations

**File:** core/sessions/user.go (L16-25)
```go
type User struct {
	Email             string
	HashedPassword    config.SecretString
	Role              UserRole
	CreatedAt         time.Time
	TokenKey          null.String
	TokenSalt         null.String
	TokenHashedSecret null.String
	UpdatedAt         time.Time
}
```

**File:** core/sessions/user.go (L27-34)
```go
type UserRole string

const (
	UserRoleAdmin UserRole = "admin"
	UserRoleEdit  UserRole = "edit"
	UserRoleRun   UserRole = "run"
	UserRoleView  UserRole = "view"
)
```

**File:** core/sessions/user.go (L86-108)
```go
func GetUserRole(role string) (UserRole, error) {
	if role == string(UserRoleAdmin) {
		return UserRoleAdmin, nil
	}
	if role == string(UserRoleEdit) {
		return UserRoleEdit, nil
	}
	if role == string(UserRoleRun) {
		return UserRoleRun, nil
	}
	if role == string(UserRoleView) {
		return UserRoleView, nil
	}

	errStr := fmt.Sprintf(
		"Invalid role: %s. Allowed roles: '%s', '%s', '%s', '%s'.",
		role,
		UserRoleAdmin,
		UserRoleEdit,
		UserRoleRun,
		UserRoleView,
	)
	return UserRole(""), pkgerrors.New(errStr)
```

**File:** core/web/resolver/auth.go (L19-43)
```go
// Authenticates the user from the session cookie and asserts at least 'run' role.
func authenticateUserCanRun(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	if session.User.Role == sessions.UserRoleView {
		return RoleNotPermittedError{session.User.Role}
	}
	return nil
}

// Authenticates the user from the session cookie and asserts at least 'edit' role.
func authenticateUserCanEdit(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	switch session.User.Role {
	case sessions.UserRoleView, sessions.UserRoleRun:
		return RoleNotPermittedError{session.User.Role}
	default:
	}
	return nil
}
```

**File:** core/web/resolver/auth.go (L46-55)
```go
func authenticateUserIsAdmin(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	if session.User.Role != sessions.UserRoleAdmin {
		return RoleNotPermittedError{session.User.Role}
	}
	return nil
}
```

**File:** core/web/auth/gql.go (L25-59)
```go
func AuthenticateGQL(authenticator Authenticator, lggr logger.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		ctx := c.Request.Context()
		session := sessions.Default(c)
		sessionID, ok := session.Get(SessionIDKey).(string)
		if !ok {
			return
		}

		user, err := authenticator.AuthorizedUserWithSession(ctx, sessionID)
		if err != nil {
			if errors.Is(err, clsessions.ErrUserSessionExpired) {
				lggr.Warnw("Failed to authenticate session", "err", err)
			} else {
				lggr.Errorw("Failed call to AuthorizedUserWithSession, unable to get user", "err", err)
			}
			return
		}

		ctx = WithGQLAuthenticatedSession(c.Request.Context(), user, sessionID)

		c.Request = c.Request.WithContext(ctx)
	}
}

// WithGQLAuthenticatedSession sets the authenticated session in the context
//
// There shouldn't be a need to do this outside of testing
func WithGQLAuthenticatedSession(ctx context.Context, user clsessions.User, sessionID string) context.Context {
	return context.WithValue(
		ctx,
		sessionUserKey{},
		&GQLSession{sessionID, &user},
	)
}
```
