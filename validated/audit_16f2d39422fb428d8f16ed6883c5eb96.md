Confirmed: `metricRoutes(authv2)` at [1](#0-0)  mounts all `/debug/pprof/*` handlers (index, cmdline, profile, heap, goroutine, trace, mutex, allocs, block, threadcreate) directly on `pprofGroup := r.Group("/debug/pprof")` with **no** role wrapper — no `auth.RequiresRunRole`, `RequiresEditRole`, or `RequiresAdminRole` call anywhere in that function, unlike every other sensitive route in `v2Routes`. `metricRoutes(authv2)` is called under the `authv2` group which only applies `auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)` [2](#0-1)  — this only requires a valid session/token, not a minimum role. `auth.Authenticate` just verifies credentials and calls `c.Next()` without checking role [3](#0-2) . A 'view'-role user authenticated via `AuthenticateBySession` or `AuthenticateByToken` therefore passes through and reaches the raw `net/http/pprof` handlers, e.g. `GET /v2/debug/pprof/heap` returning a full heap dump, comparable to `RequiresRunRole` on `/replay_from_block` and `/find_lca` in the same file.

### Title
Missing role check on `/v2/debug/pprof/*` allows low-privilege 'view' users to dump process memory/goroutines - ([File: core/web/router.go])

### Summary
`metricRoutes` mounts Go's `net/http/pprof` handlers under the `authv2` group in `v2Routes`, but only wraps them with generic session/token authentication (`auth.Authenticate`) and never applies `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` like every other privileged endpoint in the file. Any authenticated user, including one created with the lowest 'view' role, can hit `GET /v2/debug/pprof/heap`, `/goroutine`, `/cmdline`, `/trace`, `/profile`, etc.

### Finding Description
`v2Routes` builds `authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))` [2](#0-1) , then near the end calls `metricRoutes(authv2)` with the comment "Debug routes accessible via authentication" [4](#0-3) . Inside `metricRoutes`, each pprof endpoint is registered directly on `pprofGroup` without any `auth.RequiresXRole` wrapper: `pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))` [1](#0-0) . Compare this to sibling routes in the same group that correctly gate on role, e.g. `authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))` and `authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))` [5](#0-4) . `auth.Authenticate` only verifies the session/token is valid and sets the `SessionUserKey` in context, then calls `c.Next()` — it performs no role comparison [3](#0-2) . Role gating is only added when an endpoint is explicitly wrapped in `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole`, which check `user.Role` against `clsessions.UserRoleView`, `UserRoleRun`, `UserRoleAdmin` [6](#0-5) . Since pprof routes have no such wrapper, a user with `UserRoleView` (the lowest role, normally restricted to read-only, non-secret-adjacent operations) passes the bare `Authenticate` check and reaches the raw pprof handler, obtaining full heap dumps, goroutine stacks, CPU profiles, and the ability to trigger CPU/trace profiling (`/profile`, `/trace`) which can also be a minor DoS/resource-consumption vector.

### Impact Explanation
Heap and goroutine dumps can contain process memory contents, potentially including secrets held in memory (private keys, session tokens, database credentials, decrypted config values) depending on what the runtime happens to have resident, and goroutine/stack traces reveal internal architecture and could aid further attacks. This matches the Chainlink bounty class of "sensitive data / secret disclosure via authorization bypass" — an unprivileged/low-role authenticated user obtains data and a debug surface that should require at least run/edit/admin role, i.e., privileged information disclosure via a broken access-control control.

### Likelihood Explanation
The precondition is only a valid low-privilege 'view'-role Chainlink node user account (session cookie or API token) — a role explicitly intended to be highly restricted. No admin/operator access, no network-layer tricks, and no misconfiguration are required; the missing role wrapper is a straightforward code defect reachable from any authenticated view-only account, making this easily and repeatably exploitable with a single GET request.

### Recommendation
Wrap every pprof route registered in `metricRoutes` with an appropriate role-check middleware (e.g., `auth.RequiresAdminRole`, consistent with the sensitivity of memory/CPU dumps), the same way `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` gate other authv2 routes, e.g. change `pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))` to `pprofGroup.GET("/heap", auth.RequiresAdminRole(ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP)))` for all routes in `metricRoutes`.

### Proof of Concept
Go handler-level integration test (in `core/web/router_test.go` style, using existing test helpers like `cltest.NewApplication`, `cltest.CreateUserWithRole`, `client.NewTest`):
1. Boot a test `chainlink.Application` and router via `NewRouter`.
2. Create a user with `clsessions.UserRoleView` and log in to obtain a session cookie (or issue an API token for that user).
3. Send `GET /v2/debug/pprof/heap` with the view-role session cookie / API token headers.
4. Assert the response status is currently `200 OK` with pprof heap-profile content-type/body (proving the bug), and assert that after the fix it becomes `401`/`403` (`Unauthorized`/`Forbidden` JSON error) as returned by `RequiresRunRole`/`RequiresAdminRole` in `core/web/auth/auth.go`.
5. Repeat for `/v2/debug/pprof/goroutine` and `/v2/debug/pprof/cmdline` to confirm the whole `metricRoutes` group is unprotected.

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

**File:** core/web/router.go (L297-302)
```go
		rc := ReplayController{app}
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
		lcaC := LCAController{app}
		authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))
		lpSkipC := LPSkipController{app}
		authv2.POST("/lp_skip_to_block", auth.RequiresRunRole(lpSkipC.LPSkipToBlock))
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

**File:** core/web/auth/auth.go (L200-217)
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
```
