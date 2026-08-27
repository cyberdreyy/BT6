### Title
Missing role authorization gate on `/debug/vars` allows view-role users to access internal runtime data - ([File: core/web/router.go])

### Finding Description
`debugRoutes` registers `GET /debug/vars` behind only `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, which merely confirms the caller holds *any* valid session cookie via `AuthenticateBySession` and sets the authenticated user in context, but performs no role check. [1](#0-0) 
Compare this to every other sensitive endpoint in the router, e.g. `/v2/users` (`authv2.GET("/users", auth.RequiresAdminRole(uc.Index))`), key export/import/delete routes, and `/v2/log` (`auth.RequiresAdminRole(lgc.Patch)`), all of which wrap the handler with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole` on top of `Authenticate`. [2](#0-1) [3](#0-2) 
`AuthenticateBySession` itself only validates the session ID against the store and sets `SessionUserKey`; it never inspects `user.Role`. [4](#0-3) 
Role gating is only enforced by the separate `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrapper functions, which are simply not applied to `/debug/vars`. [5](#0-4) 
As a result, any user with a valid session — including one created with `UserRoleView` — passes `Authenticate` and reaches `expvar.Handler()` directly, dumping the process's exported runtime variables (default Go `expvar` publishes `cmdline` and `memstats`, and any custom vars registered by dependencies), whereas an equivalent-sensitivity admin endpoint like `/v2/users` requires `UserRoleAdmin`.

### Impact Explanation
This is an authorization/role-exactness gap: a low-privilege authenticated identity (view-role session) obtains data intended to be restricted to admin-level operational debugging. Concrete impact is disclosure of the node process's command-line arguments and memory statistics (and any other package that registers an `expvar.Var`), which can leak internal paths, flags, or other runtime state not meant for view-role consumption — matching a "sensitive data exposure via missing authorization check" bounty class rather than a critical secrets leak, since no direct private key or credential is published via stdlib `expvar` in this codebase (no `expvar.Publish`/`expvar.NewString` custom vars were found).

### Likelihood Explanation
Precondition is only a valid session cookie of any role (view is the lowest tier); no token, no admin/edit/run privileges are required. This is trivially and repeatably exploitable by any user who can log in with a view-only account, e.g. via `POST /sessions` then `GET /debug/vars`.

### Recommendation
Wrap the `/debug/vars` route with `auth.RequiresAdminRole` (consistent with other operationally sensitive endpoints like `/v2/log` PATCH and `/v2/users`), e.g.:
```go
group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler()(c) }))
```

### Proof of Concept
1. In a `router_test.go`-style handler integration test, build the router via `NewRouter` with a test `Authenticator`/session store.
2. Create a session for a user with `Role: clsessions.UserRoleView` and set the session cookie (`auth.SessionName`, `auth.SessionIDKey`) via `httptest`.
3. Issue `GET /debug/vars` with that cookie.
4. Assert the response status is `200` and body contains expvar JSON (`cmdline`, `memstats`) — proving a view-role session bypasses any role gate.
5. As a control, issue `GET /v2/users` with the same view-role cookie and assert `403 Forbidden` (via `auth.RequiresAdminRole`), demonstrating the inconsistency: `/v2/users` correctly rejects view-role while `/debug/vars` does not.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L251-254)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
```

**File:** core/web/router.go (L410-412)
```go
		lgc := LogController{app}
		authv2.GET("/log", lgc.Get)
		authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))
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
