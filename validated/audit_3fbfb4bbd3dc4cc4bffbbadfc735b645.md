### Title
Missing role check on `/debug/vars` allows any authenticated (view-role) user to read process expvar data - ([File: core/web/router.go])

### Summary
`debugRoutes` registers `GET /debug/vars` behind only `auth.Authenticate(..., auth.AuthenticateBySession)`, with no `RequiresEditRole`/`RequiresAdminRole`/`RequiresRunRole` wrapper, unlike almost every other privileged route in `v2Routes`. Any user who can log in — including a `UserRoleView` account, the lowest privilege level — can call this endpoint and receive the full `expvar` output (Go runtime `memstats`, `cmdline`, and any custom-registered expvars).

### Finding Description
`debugRoutes` is:
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
``` [1](#0-0) 

`auth.Authenticate` only verifies that a valid session/token authenticates *some* user and stores it in context via `c.Set(SessionUserKey, &user)`; it performs no role comparison. [2](#0-1) 

Role gating in this codebase is implemented separately via `RequiresRunRole`, `RequiresEditRole`, and `RequiresAdminRole`, which explicitly check `user.Role` and reject `UserRoleView` (and sometimes `UserRoleRun`) with 401/403. [3](#0-2) 

Every other sensitive route in `v2Routes` (key export/import, admin user management, chain/config controllers) is wrapped with one of these role-check functions, e.g. `authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))`. [4](#0-3) 

`debugRoutes` is the sole exception — it uses `auth.Authenticate` directly with no role wrapper, so an authenticated `UserRoleView` session cookie is sufficient to reach `expvar.Handler()`, which returns whatever variables are published via Go's `expvar` package (process command line, memory stats, and any application-registered counters/gauges).

### Impact Explanation
A view-role (lowest-privilege) user, or any low-privilege session cookie holder, can read internal runtime state intended for privileged debugging (`/debug/vars`), which is inconsistent with the project's own role model that reserves this kind of route for elevated roles elsewhere. This matches an authorization/role-bypass class issue: excessive information disclosure to an under-privileged authenticated principal. The direct impact is limited to whatever is exposed through `expvar` (Go `memstats`, `cmdline`, and any custom counters the node registers) rather than secrets/keys, so it is an information-disclosure/least-privilege violation rather than a critical key leak.

### Likelihood Explanation
The only precondition is possessing a valid session cookie for any user account, including the lowest `UserRoleView` role — this is fully attacker-reachable per the stated threat model (view-role user with a valid session). The bug is deterministic and trivially repeatable: a single `GET /debug/vars` request with the session cookie set succeeds every time, since no role check exists in the code path.

### Recommendation
Wrap the `/debug/vars` route with `auth.RequiresAdminRole` (or at minimum `auth.RequiresEditRole`), matching the treatment given to other sensitive debug/admin endpoints such as `/v2/log` PATCH:
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler().ServeHTTP(c.Writer, c.Request) }))
}
```

### Proof of Concept
Go handler-level integration test plan (in `core/web`):
1. Build the app/test harness used elsewhere (`cltest.NewApplication`), create a session for a user with `sessions.UserRoleView`.
2. Construct the router via `web.NewRouter(app, nil)` and start an `httptest.Server`.
3. Perform `POST /sessions` login as the view-role user to obtain the session cookie (or directly seed a session via the test auth provider).
4. Send `GET /debug/vars` with that cookie attached.
5. Assert response status is `200 OK` (not `401`/`403`) and that the JSON body contains expvar keys (e.g. `"cmdline"`, `"memstats"`), demonstrating disclosure of runtime data to a view-only user.
6. As a control, add the recommended `RequiresAdminRole` wrapper and re-run the test, asserting the response becomes `403 Forbidden` for the view-role user and `200` only for an admin-role session.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L410-412)
```go
		lgc := LogController{app}
		authv2.GET("/log", lgc.Get)
		authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))
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
