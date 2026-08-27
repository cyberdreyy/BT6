### Title
`/debug/vars` endpoint reachable by any authenticated session regardless of role, exposing expvar memstats/cmdline - ([File: core/web/router.go])

### Summary
`debugRoutes` mounts `GET /debug/vars` behind only `auth.Authenticate(..., auth.AuthenticateBySession)`, with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike nearly every other sensitive route in `v2Routes`. Any authenticated user, including one with the lowest privilege `UserRoleView`, can therefore reach `expvar.Handler()` and receive runtime memstats and command-line arguments (`os.Args`).

### Finding Description
`debugRoutes` builds the group as: [1](#0-0) 
This only requires session authentication via `auth.AuthenticateBySession`, which sets the authenticated user in context without any role check: [2](#0-1) 
The `Authenticate` middleware itself performs no role gating — it simply verifies identity and calls `c.Next()`: [3](#0-2) 
By contrast, every sensitive handler elsewhere in `v2Routes` (keys, jobs, bridges, users, etc.) is explicitly wrapped with `auth.RequiresEditRole` or `auth.RequiresAdminRole`, and even the debug-adjacent `pprof` routes registered via `metricRoutes(authv2)` are placed inside the `authv2` group that at least requires a valid session (though also without additional role gating) — but the `/debug/vars` route specifically bypasses even that grouping and only checks session authentication, no role restriction at all: [4](#0-3) [5](#0-4) 

Since a `UserRoleView` session passes `AuthenticateBySession` successfully (the function only checks session validity, not role), such a user reaches `expvar.Handler()` and gets a 200 response with JSON containing `cmdline` (`os.Args`, revealing config file paths, flags, potentially embedded secrets/paths) and `memstats` (heap/GC internals, useful for fingerprinting and infra reconnaissance).

### Impact Explanation
This is an information disclosure via authorization/role-check gap: a low-privileged (`view`-role) authenticated user can obtain command-line arguments and runtime memory statistics that were intended to be more restricted operationally. It does not by itself yield fund movement, key disclosure, or job execution, so it falls into a low/informational bounty class (broken access control / information disclosure), not full node compromise. It could aid further reconnaissance (e.g., revealing filesystem paths, flags used to start the node) but no secret/key material is confirmed exposed here.

### Likelihood Explanation
Any valid `view`-role credential (the lowest privilege tier explicitly documented in the role model) is sufficient — no edit/admin/token elevation required. The request is a single unauthenticated-role-check `GET /debug/vars` with a valid session cookie, trivially repeatable, and the code path unambiguously lacks any `RequiresEditRole`/`RequiresAdminRole` wrapper as confirmed by direct code reading.

### Recommendation
Wrap the `/debug/vars` route with `auth.RequiresAdminRole` (or at minimum `auth.RequiresEditRole`), consistent with the rest of the sensitive routes in `v2Routes`, e.g.:
```go
group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler()(c.Writer, c.Request) }))
```

### Proof of Concept
Go handler-level integration test plan (in `core/web` test package, following patterns in existing router tests):
1. Set up a test app/router via `NewRouter` (as done in `core/web/router_test.go`-style setup) with a seeded user of `Role: clsessions.UserRoleView`.
2. Log in as that user via `POST /sessions` to obtain a session cookie (or directly set the session as done in other auth tests).
3. Issue `GET /debug/vars` with the view-role session cookie.
4. Assert response status is `http.StatusOK` and body is valid JSON containing `"cmdline"` and `"memstats"` keys — demonstrating the endpoint returns data instead of `403 Forbidden`.
5. As a comparison assertion, confirm that hitting an edit/admin-gated route (e.g., `POST /v2/bridge_types`) with the same view-role session returns `401`/`403`, proving other routes correctly enforce role checks while `/debug/vars` does not.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L185-199)
```go
func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	pprofGroup.POST("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/trace", ginHandlerFromHTTP(pprof.Trace))
	pprofGroup.GET("/allocs", ginHandlerFromHTTP(pprof.Handler("allocs").ServeHTTP))
	pprofGroup.GET("/block", ginHandlerFromHTTP(pprof.Handler("block").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/mutex", ginHandlerFromHTTP(pprof.Handler("mutex").ServeHTTP))
	pprofGroup.GET("/threadcreate", ginHandlerFromHTTP(pprof.Handler("threadcreate").ServeHTTP))
}
```

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
