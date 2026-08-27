### Title
View-role authenticated user can access pprof debug endpoints (heap/goroutine dumps) with no role check - (File: core/web/router.go)

### Summary
`metricRoutes` is mounted inside the `authv2` group with only `auth.Authenticate` (session or API token) applied, and unlike every other sensitive endpoint in `v2Routes`, it has no `auth.RequiresEditRole`/`auth.RequiresAdminRole`/`auth.RequiresRunRole` wrapper. Any authenticated user regardless of role — including the lowest-privileged `UserRoleView` — can hit `/v2/debug/pprof/heap`, `/goroutine`, `/profile`, `/trace`, etc. and pull raw in-process memory/goroutine dumps.

### Finding Description
`v2Routes` builds the `authv2` group with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` [1](#0-0) , which only verifies that the caller is *some* valid user (any of the four roles: view, run, edit, admin) — it performs no role-based authorization. Every other route added to `authv2` explicitly wraps its handler with `auth.RequiresEditRole`, `auth.RequiresAdminRole`, or `auth.RequiresRunRole` for anything sensitive (key export/import/delete, job mutation, transfers, etc.) [2](#0-1) . `metricRoutes(authv2)` is called directly with the comment "Debug routes accessible via authentication" and no such role wrapper [3](#0-2) .

`metricRoutes` registers the full stdlib `net/http/pprof` handler set — index, cmdline, profile, symbol, trace, allocs, block, goroutine, heap, mutex, threadcreate — under `/debug/pprof` on the `authv2` router group with no additional middleware [4](#0-3) .

Checking `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` confirms these are the only mechanisms that reject `UserRoleView`: `RequiresRunRole` aborts with 401 if `user.Role == clsessions.UserRoleView` [5](#0-4) , and none of these wrappers are applied to the pprof routes. `auth.Authenticate` itself performs no role check at all — it merely runs each `authMethod` and sets the user in context [6](#0-5) .

Consequently a user with `UserRoleView` (the lowest role, intended for read-only dashboard access) can call `GET /v2/debug/pprof/heap` and receive a full heap profile of the running chainlink node process. Heap/goroutine/full-goroutine-stack pprof dumps commonly contain byte slices and strings currently resident in memory — this can include decrypted private keys, unlocked wallet material, session tokens, API secrets, and database connection strings that are transiently held in memory during normal node operation.

### Impact Explanation
This maps to Chainlink's "sensitive data disclosure / broken access control" impact class: an authenticated but minimally-privileged (`view`) user obtains debug introspection capability that should require, at minimum, `admin`-level trust. Successful exploitation could leak decrypted key material or session/API secrets that are resident in the Go heap, which is a direct violation of `SECRET_CONFINEMENT` and `AUTHORIZATION_EXACTNESS`. Actual key exposure is dependent on runtime state (whether such secrets happen to be live in memory at capture time), but the debug endpoint itself is unambiguously reachable by an under-privileged role, which is the core authorization defect.

### Likelihood Explanation
Preconditions: only a valid `view`-role session or API token is required — the lowest privilege level a node operator can grant to any authenticated user. No admin/host access needed. The request is a simple unauthenticated-of-role `GET` to a well-known, always-registered path (`/v2/debug/pprof/heap`, `/goroutine`, `/profile`, etc.), fully repeatable and requiring no timing or race conditions.

### Recommendation
Wrap `metricRoutes(authv2)` with `auth.RequiresAdminRole` (consistent with how key export/import/delete and other genuinely sensitive operations are gated in the same file), e.g. register each pprof handler as `auth.RequiresAdminRole(ginHandlerFromHTTP(pprof.Handler(...).ServeHTTP))`, or move the group to a separate route group requiring admin role before calling `metricRoutes`.

### Proof of Concept
Go handler-level integration test plan (using `httptest` + the existing router construction used in `core/web` tests):
1. Build a test app/router via the same helpers used in `core/web/router_test.go` (or equivalent), creating three authenticated sessions/tokens: `UserRoleView`, `UserRoleRun`, `UserRoleAdmin`.
2. For each of the `metricRoutes` sub-paths (`/v2/debug/pprof/`, `/cmdline`, `/profile`, `/symbol`, `/trace`, `/allocs`, `/block`, `/goroutine`, `/heap`, `/mutex`, `/threadcreate`), issue a `GET` (and `POST` for `/symbol`) request authenticated as the `UserRoleView` client.
3. Assert current (vulnerable) behavior: HTTP 200 is returned for the view-role client on all these paths (proving the missing role check), instead of the expected `403 Forbidden` / `401 Unauthorized` that `auth.RequiresAdminRole`/`RequiresEditRole` would produce for other sensitive routes.
4. As a regression test after the fix, assert `UserRoleView` and `UserRoleRun` (and `UserRoleEdit` if intended admin-only) receive 401/403, while `UserRoleAdmin` receives 200.
5. Optionally, for a stronger PoC, call `GET /v2/debug/pprof/heap?debug=1` and grep the response body for patterns resembling PEM/hex private keys or the `X-API-SECRET`/session-cookie values used in the same test process, to demonstrate concrete secret residency in the dump.

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

**File:** core/web/router.go (L317-320)
```go
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/auth/auth.go (L157-175)
```go
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

**File:** core/web/auth/auth.go (L202-217)
```go
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
```
