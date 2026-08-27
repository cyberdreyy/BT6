### Title
View-role authenticated user can dump full process heap/goroutine memory via unprotected `/v2/debug/pprof/*` routes - ([File: core/web/router.go])

### Summary
`metricRoutes(authv2)` is mounted under the fully-authenticated `authv2` group but, unlike every other sensitive endpoint in `v2Routes`, is not wrapped in any `auth.Requires*Role` check. Any user who can pass base session/token authentication—including the lowest-privilege `view` role—can hit `GET /v2/debug/pprof/heap` or `/v2/debug/pprof/goroutine` and receive a raw memory/goroutine dump of the running node process.

### Finding Description
In `v2Routes`, the `authv2` group only requires `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)`, which merely confirms a valid session/token exists and sets the authenticated user in context — it performs no role check [1](#0-0) . Every other endpoint that should be gated by privilege explicitly wraps its handler with `auth.RequiresEditRole`, `auth.RequiresRunRole`, or `auth.RequiresAdminRole` (e.g. key export/import, jobs create/update/delete, transfers, users) [2](#0-1) . `metricRoutes(authv2)` is called with no such wrapper: [3](#0-2) .

`metricRoutes` itself registers the standard `net/http/pprof` handlers directly, including `/debug/pprof/heap` and `/debug/pprof/goroutine`, with zero additional access control inside the function: [4](#0-3) .

The role hierarchy defined in `core/web/auth/auth.go` shows `view` is the lowest role and is only blocked from `Run`/`Edit`/`Admin`-gated routes via `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` [5](#0-4) . Since pprof routes have no such wrapper, a `view`-role session or API token passes straight through `auth.Authenticate` and reaches the raw pprof handlers, which return the process's heap allocation records or goroutine stack traces (potentially containing key material, decrypted secrets, or session tokens resident in memory) as a full HTTP 200 response.

This is a genuine authorization gap (missing least-privilege gating on a sensitive read endpoint), not a misconfiguration or infra concern — it is reachable purely through the standard authenticated HTTP API with a `view`-role credential.

### Impact Explanation
Impact is scoped to **secret material disclosure via memory dump**: a `view`-role user (the lowest privilege tier, intended only for read-only dashboard access) can retrieve heap/goroutine dumps of the running `chainlink` node process. Go heap and goroutine dumps commonly retain byte slices, strings, and stack frames that may include decrypted private keys, session cookies, database credentials, or API secrets still resident in memory, even though the presenter/JSON layers elsewhere in the API redact such fields. This maps to a credential/secret-exposure impact class, escalating a low-privilege authenticated user's access far beyond their intended read-only role.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs a valid `view`-role session cookie or API token — the lowest privilege level grantable in the system — and network access to the node's web server (the same access needed for any other v2 API call). No admin/edit privilege, no misconfiguration, and no additional exploitation steps are required; a single unauthenticated-role-check-bypass HTTP GET completes the attack, and it is fully repeatable on demand.

### Recommendation
Wrap all `metricRoutes` handlers (and ideally the `/debug/vars` route as well, though that one already requires session auth) with `auth.RequiresAdminRole` (or at minimum `auth.RequiresEditRole`), consistent with how every other sensitive introspection/export endpoint in `v2Routes` is protected. Concretely, change the call in `router.go` to pass role-wrapped handlers, e.g. `pprofGroup.GET("/heap", auth.RequiresAdminRole(ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP)))` for each pprof sub-route.

### Proof of Concept
Go handler-level integration test plan (in `core/web` test package, following the pattern of existing router tests):
1. Set up the test app/router via the existing `setupJobsControllerTests`-style helper (or equivalent `web.NewRouter` test harness) with a seeded user of role `sessions.UserRoleView`.
2. Authenticate as that view-role user (obtain session cookie via `POST /sessions` or generate an API token) and issue `GET /v2/debug/pprof/heap` and `GET /v2/debug/pprof/goroutine` with that credential.
3. Assert current (vulnerable) behavior: response status is `200 OK` with a non-empty pprof binary/protobuf body — demonstrating unauthorized data disclosure.
4. After applying the fix (wrapping routes with `auth.RequiresAdminRole`), re-run the same request and assert `403 Forbidden` for the view-role user, and `200 OK` only for a user seeded with `sessions.UserRoleAdmin`.

### Citations

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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L251-257)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)
```

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
