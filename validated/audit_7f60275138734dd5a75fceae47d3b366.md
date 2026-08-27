### Title
GET /debug/vars accessible to lowest-privilege (view-role) authenticated users with no role check, disclosing internal expvar state - ([File: core/web/router.go])

### Summary
`debugRoutes` in `core/web/router.go` registers `GET /debug/vars` behind only `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, with no `auth.RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` wrapper, unlike nearly every other sensitive endpoint in the same file. Any authenticated user, including one with `sessions.UserRoleView` (the lowest role), can call this route and receive the raw Go `expvar` dump.

### Finding Description
The route is defined as: [1](#0-0) 

`auth.Authenticate` only runs the supplied `authMethod`s (here `AuthenticateBySession`) and calls `c.Next()` on success — it performs no role comparison at all: [2](#0-1) 

`AuthenticateBySession` merely resolves the session cookie to a `sessions.User` (of any role) and sets it in the Gin context; it never checks `user.Role`: [3](#0-2) 

Role enforcement is implemented separately as wrapper functions (`RequiresRunRole`, `RequiresEditRole`, `RequiresAdminRole`) that must be explicitly applied per-handler: [4](#0-3) 

Comparing to the rest of `router.go`, essentially every other authenticated handler is wrapped with one of these role checks (e.g. `authv2.GET("/users", auth.RequiresAdminRole(uc.Index))`, `authv2.POST("/keys/eth", auth.RequiresEditRole(...))`, `authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(...))`): [5](#0-4) [6](#0-5) 

`debugRoutes`'s `/debug/vars` handler is the bare `expvar.Handler()` with zero role wrapper, so it is reachable by any valid session regardless of `UserRoleView`, `UserRoleRun`, `UserRoleEdit`, or `UserRoleAdmin`. Note that `metricRoutes` (pprof endpoints) is registered inside the `authv2` group which itself sits behind session/token auth but is likewise not role-gated per-route beyond the group-level session check — however the question specifically scopes to `/debug/vars`.

### Impact Explanation
`expvar` exposes process-level Go runtime state (registered `expvar.Var`s — typically memstats, and any custom counters the application registers) to any authenticated user, including the lowest-privileged `view` role, whose intended access is read-only over business data, not internal runtime/operational metrics. This is an authorization-granularity gap (missing minimum-role enforcement) rather than a full compromise: it does not expose secrets/keys, but it violates the "authorization is exact" invariant and leaks internal state (e.g. memory stats, potentially custom counters) to under-privileged accounts, aiding further reconnaissance/targeting of the node. This maps to a low/informational internal-information-disclosure impact class rather than fund loss or key compromise.

### Likelihood Explanation
Trivial and fully repeatable: any valid session cookie for a user provisioned with `view` role (the lowest privilege tier, explicitly meant to be read-only/limited) is sufficient. No token forgery, no timing, no race condition — a single authenticated `GET /debug/vars` request suffices.

### Recommendation
Wrap the `/debug/vars` route with an explicit minimum-role check, e.g. `auth.RequiresAdminRole` (matching the sensitivity level of other admin/ops-only introspection routes), in `core/web/router.go`:
```go
group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler().ServeHTTP(c.Writer, c.Request) }))
```
or equivalently gate the whole `/debug` group with a role-check middleware, consistent with how `authv2` sub-routes enforce `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole`.

### Proof of Concept
Go handler-level integration test (pattern following `core/web/auth/auth_test.go`):
1. Build a `gin.Engine` with `debugRoutes(app, group)` wired against a stub `Authenticator`/`AuthenticationProvider` returning a `sessions.User{Role: sessions.UserRoleView}` for a valid session ID.
2. Issue `GET /debug/vars` with a valid session cookie for that view-role user.
3. Assert current behavior: `http.StatusOK` and non-empty JSON body (expvar dump) is returned — demonstrating the gap.
4. After applying the fix (wrapping with `auth.RequiresAdminRole`), re-run the same test and assert `http.StatusForbidden` for the view-role user, and `http.StatusOK` only for a `sessions.UserRoleAdmin` session.

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

**File:** core/web/router.go (L297-298)
```go
		rc := ReplayController{app}
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
```

**File:** core/web/auth/auth.go (L52-71)
```go
// AuthenticateBySession authenticates the request by the session cookie.
//
// Implements authMethod
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

**File:** core/web/auth/auth.go (L155-175)
```go
// Authenticate is middleware which authenticates the request by attempting to
// authenticate using all the provided methods.
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
}
```

**File:** core/web/auth/auth.go (L200-255)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}

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
