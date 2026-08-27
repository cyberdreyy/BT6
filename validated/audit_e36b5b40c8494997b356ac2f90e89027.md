### Title
Missing role-based authorization on `GET /debug/vars` allows view-role users to access Go `expvar` runtime internals - (File: core/web/router.go)

### Summary
`debugRoutes` mounts the standard library `expvar.Handler()` at `/debug/vars` behind only `auth.Authenticate(..., auth.AuthenticateBySession)`, with no `auth.RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` wrapper. Any authenticated session, including the lowest-privileged `view` role, can therefore reach this endpoint and dump `expvar`-registered runtime internals (memstats, cmdline, and any custom-published vars), whereas every comparably sensitive endpoint in the router (keys export, users, chains/nodes admin data, `pprof` under `metricRoutes`) is explicitly gated by role.

### Finding Description
In `core/web/router.go`, `debugRoutes` is defined as: [1](#0-0) 
This only requires that the request pass `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, i.e. a valid session cookie for *any* role. Looking at `core/web/auth/auth.go`, the role hierarchy is `UserRoleView < UserRoleRun < UserRoleEdit < UserRoleAdmin`, enforced by separate wrapper functions `RequiresRunRole`, `RequiresEditRole`, and `RequiresAdminRole`: [2](#0-1) 
`Authenticate` itself performs no role check — it only verifies the session/token is valid and sets the authenticated user in the context: [3](#0-2) 
Since `debugRoutes` never applies any of the `RequiresXRole` wrappers to `group.GET("/vars", expvar.Handler())`, a session belonging to a user with `Role: view` passes `Authenticate` successfully and reaches the raw `expvar.Handler()`, which serves the process's registered expvar variables (including Go's built-in `memstats` and `cmdline`) as JSON. Elsewhere in the same file, functionally similar debug/profiling data (`pprof`) is deliberately routed only under authenticated admin/edit-gated groups (`metricRoutes(authv2)` inside the block that otherwise gates keys/users/chains endpoints with `RequiresAdminRole`/`RequiresEditRole`), showing an inconsistent authorization posture for `/debug/vars` specifically.

### Impact Explanation
A `view`-role user (the lowest privilege tier, intended only for read-only dashboards) can retrieve process runtime internals via `GET /debug/vars`: Go `memstats` (heap/GC internals useful for fingerprinting and potential DoS/timing reconnaissance) and `cmdline` (the process's command-line arguments). If the node is started with sensitive values passed as CLI flags (e.g., database DSN, API secrets) rather than exclusively via env vars/secrets files, those would be disclosed to a low-privileged authenticated user, constituting credential/secret exposure to an under-privileged actor. This matches a "sensitive information disclosure via missing authorization" class finding — the exact secret content exposed depends on operator deployment (what is passed via cmdline args), but the missing role gate itself is a confirmed authorization defect against the "view role should not access admin-level debug internals" invariant.

### Likelihood Explanation
Highly feasible and repeatable: it only requires one valid session with `role=view`, which is the lowest privilege level obtainable by a legitimate authenticated (but restricted) user or API-token holder. No additional exploitation steps, timing, or race conditions are needed — a single `GET /debug/vars` request with the session cookie set suffices.

### Recommendation
Wrap the `/debug/vars` route with an appropriate role-check middleware consistent with the sensitivity of the data exposed, e.g.:
```go
group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler().ServeHTTP(c.Writer, c.Request) }))
```
or at minimum `auth.RequiresEditRole`, matching the treatment given to `pprof` routes and other admin-sensitive endpoints elsewhere in `router.go`.

### Proof of Concept
Go handler-level integration test plan (in `core/web` test package, alongside existing router tests):
1. Build the router via `web.NewRouter` (or reuse test helpers used by existing `router_test.go`/controller tests) with a mocked `AuthenticationProvider`.
2. Create a session for a user with `Role: clsessions.UserRoleView` and set the session cookie (`auth.SessionName`/`SessionIDKey`) as done in other authenticated controller tests.
3. Issue `GET /debug/vars` with that view-role session cookie.
4. Assert the response status is `200 OK` and the body contains `expvar`-standard keys (`"cmdline"`, `"memstats"`), documenting that a view-role session can retrieve runtime internals.
5. Add a second assertion for the expected/fixed behavior: after applying `auth.RequiresAdminRole` (or similar) to the route, the same request with a view-role session should return `403 Forbidden`, while an admin-role session still returns `200`.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
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
