### Title
View-role authenticated user can access `net/http/pprof` debug endpoints (heap/goroutine/cmdline/trace dumps) without any role check - ([File: core/web/router.go])

### Summary
`metricRoutes(authv2)` mounts the full `net/http/pprof` handler set under `/v2/debug/pprof/*` inside the `authv2` route group, which only enforces authentication (`auth.Authenticate` with `AuthenticateByToken`/`AuthenticateBySession`) and applies no role gate. Every other sensitive endpoint in the same group (key export/import, user management, etc.) is explicitly wrapped with `auth.RequiresAdminRole` or `auth.RequiresEditRole`, but the pprof routes are registered bare, so any authenticated user of any role — including `UserRoleView` — can reach them.

### Finding Description
In `core/web/router.go`, `v2Routes` builds `authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))` [1](#0-0) . This group is used for both sensitive admin actions (each explicitly wrapped with `auth.RequiresAdminRole`/`auth.RequiresEditRole`, e.g. `authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))`) and unprotected reads. At the end of the block, `metricRoutes(authv2)` is invoked with a comment "Debug routes accessible via authentication" [2](#0-1) .

`metricRoutes` registers `net/http/pprof` handlers directly with no role wrapper at all: `pprofGroup.GET("/heap", ...)`, `/goroutine`, `/cmdline`, `/trace`, `/profile`, `/allocs`, `/mutex`, `/threadcreate`, `/symbol` [3](#0-2) .

`auth.Authenticate` only verifies that a request carries a valid session or API token and sets the authenticated user in context via `c.Set(SessionUserKey, &user)`, without checking `user.Role` [4](#0-3) . Role enforcement only happens when a handler is explicitly wrapped by `auth.RequiresRunRole`, `auth.RequiresEditRole`, or `auth.RequiresAdminRole` [5](#0-4) , none of which are applied to the pprof routes. Consequently, a `UserRoleView` (the lowest role, view-only) session or API token is sufficient to hit `GET /v2/debug/pprof/heap`, `/goroutine`, `/trace`, `/profile`, `/cmdline` and get a 200 response with a full heap/goroutine dump or CPU profile of the node process, since nothing in the request path checks the role before invoking the pprof handler.

### Impact Explanation
Heap and goroutine dumps from `pprof` can contain in-memory secrets that the node process holds decrypted at runtime — e.g., decrypted keystore material, session tokens, in-flight request bodies/headers (including other users' API secrets), and internal state. `pprof/cmdline` and `pprof/trace` similarly leak process/environment information useful for further attacks. This matches the bounty impact class of "unauthorized information disclosure of sensitive data" and represents a privilege/role-boundary violation, since the codebase's own convention (explicit `RequiresAdminRole` wrapping on all sensitive `/v2/*` endpoints) shows this class of endpoint was intended to be gated but the pprof group was overlooked.

### Likelihood Explanation
Minimal precondition: any valid authenticated principal, including the lowest privilege `UserRoleView`, obtained via a normal session login or a view-scoped API token. No admin/edit/run role is required. The request is a simple unauthenticated-role `GET /v2/debug/pprof/heap` (or `/goroutine`, `/trace`, etc.) once authenticated, fully reproducible and repeatable at will (e.g., to continuously exfiltrate memory contents over time).

### Recommendation
Wrap `metricRoutes` (or each of its routes) with `auth.RequiresAdminRole`, consistent with how other sensitive endpoints in `v2Routes` are protected, e.g.:
```go
metricRoutes(authv2) // change to apply RequiresAdminRole inside metricRoutes to each route,
// or change the call site to only mount metricRoutes under an admin-gated subgroup.
```
Concretely, in `core/web/router.go`, change each `pprofGroup.GET(...)`/`POST(...)` registration in `metricRoutes` to wrap the handler with `auth.RequiresAdminRole(...)`.

### Proof of Concept
Go handler-level integration test (using existing test harness patterns from `core/web` tests):
1. Set up a test application/router via the existing `cltest` test helpers used elsewhere in `core/web` tests (e.g., `setupUSDCMocks`-style `cltest.NewApplication` + `web.Router`).
2. Create a user with `Role: sessions.UserRoleView` and obtain a valid session cookie via the sessions controller's login flow (or directly seed session store), analogous to how `sessionRoutes`/`ldapauth`/`localauth` tests create sessions.
3. Issue `GET /v2/debug/pprof/heap` with the view-role session cookie attached.
4. Assert the response status is `200 OK` (pprof heap profile content-type `application/octet-stream`), not `401`/`403`.
5. As a control, issue the same request against an existing admin-gated route (e.g., `GET /v2/keys/eth/export/:address`) with the same view-role session and assert it returns `403 Forbidden` via `auth.RequiresAdminRole`, demonstrating the inconsistency: admin-only routes correctly reject the view-role user while pprof routes do not.

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
