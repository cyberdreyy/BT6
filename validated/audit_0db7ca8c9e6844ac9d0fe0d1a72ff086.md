### Title
`debugRoutes` exposes `/debug/vars` (expvar internals) to any authenticated session with no role check - (File: core/web/router.go)

### Summary
`debugRoutes` mounts `GET /debug/vars` behind only `auth.Authenticate(..., auth.AuthenticateBySession)`, with no `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrapper. Any user with a valid session, including the lowest-privilege `UserRoleView` account, can reach the `expvar.Handler()` and dump internal runtime state (goroutine counts, memstats, and any custom-exported vars).

### Finding Description
`debugRoutes` is registered directly on the top-level `api` group in `NewRouter`: [1](#0-0) . Its implementation only requires a valid authenticated session, with no role gate: [2](#0-1) .

`auth.Authenticate` with `AuthenticateBySession` only verifies that a session cookie maps to an existing `clsessions.User` and stores it in context; it performs no role comparison at all: [3](#0-2)  and [4](#0-3) . Role enforcement in this codebase is implemented as a *separate*, explicit wrapper — `RequiresRunRole`, `RequiresEditRole`, `RequiresAdminRole` — that must be applied on top of `Authenticate` to reject `UserRoleView` (or lower) users: [5](#0-4) . Every other sensitive handler in `router.go` (e.g. `/v2/replay_from_block`, `/v2/keys/*`, `/v2/log`) is wrapped with one of these role-check functions, e.g. [6](#0-5) , but `debugRoutes` is not, leaving `/debug/vars` reachable by a plain authenticated (view-role) session.

Exploit flow: an attacker holding valid `UserRoleView` credentials logs in to obtain a session cookie via `POST /sessions` (`sessionRoutes`), then issues `GET /debug/vars` with that cookie. `AuthenticateBySession` succeeds (any valid user, any role), no role wrapper exists, so `expvar.Handler()` executes and returns the full expvar JSON (goroutine stats, GC/memstats, and any registered custom vars) to the view-role user.

### Impact Explanation
This is an information-disclosure issue: a `UserRoleView` account (or any account whose intended privilege is below run/edit) can obtain internal runtime state — process memory statistics, goroutine counts, and custom-registered expvar counters. This matches Chainlink's "sensitive information disclosure to a lower-privileged authenticated user" impact class. It does not by itself allow fund movement, job execution, or key extraction, but it does violate the intended authorization boundary (debug/operational internals should require at least run/edit role, consistent with `metricRoutes`'s pprof endpoints being placed behind the role-gated `authv2` group at [7](#0-6) ).

### Likelihood Explanation
Requires only a valid `UserRoleView` session cookie — the lowest privilege authenticated role in the system. No additional preconditions, misconfiguration, or race conditions are needed; the missing role check is unconditional and always reachable once authenticated. Trivially repeatable with a single HTTP GET.

### Recommendation
Wrap the `/debug/vars` route with an explicit role check, e.g. `auth.RequiresRunRole` (or `RequiresEditRole`, matching the sensitivity level chosen for other debug/metrics endpoints), consistent with how `metricRoutes` pprof endpoints are placed inside the role-gated `authv2` group:
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
    group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
    group.GET("/vars", auth.RequiresRunRole(func(c *gin.Context) { expvar.Handler().ServeHTTP(c.Writer, c.Request) }))
}
```

### Proof of Concept
Handler-level integration test plan (Go, using `httptest` + the existing test app helpers in `core/web`):
1. Set up a test `chainlink.Application` and router via `NewRouter`, as done in existing `core/web/*_test.go` files.
2. Create a user with `clsessions.UserRoleView` and authenticate via `POST /sessions` to obtain a session cookie.
3. Issue `GET /debug/vars` with that cookie attached.
4. Assert response status is `200 OK` and body is valid expvar JSON (contains `memstats`, `cmdline` keys) — demonstrating the missing role check.
5. As a control, assert the same view-role session gets `401`/`403` on an admin/edit-gated endpoint like `POST /v2/keys/eth` to confirm the role system is functioning elsewhere but not applied to `/debug/vars`.
6. After applying the recommended fix, re-run step 3/4 and assert `401 Unauthorized` for the view-role user, and `200 OK` for a `UserRoleRun`/`UserRoleEdit` session.

### Citations

**File:** core/web/router.go (L86-91)
```go

	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
```

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
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

**File:** core/web/router.go (L444-447)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
	}
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
