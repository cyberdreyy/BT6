### Title
Debug/pprof routes registered without role check, exposing process memory and command-line to any authenticated (including view-role) user - (File: core/web/router.go)

### Summary
The pprof debug endpoints registered by `metricRoutes` are mounted inside the `authv2` group but, unlike every other sensitive handler in that group, are not wrapped with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole`. Any user who can authenticate with any role (including the lowest `view` role, or a run/edit API token) can reach `/v2/debug/pprof/cmdline`, `/heap`, `/goroutine`, `/trace`, etc. Note: the question's stated root cause in `cookies.FindSessionCookie` does not hold up — that function is a trivial cookie lookup helper not used anywhere in the actual server-side session/role resolution path (`auth.AuthenticateBySession` resolves the session via `gin-contrib/sessions`, not via `cookies.FindSessionCookie`), so the "multiple clsession cookies" exploit premise is not applicable here.

### Finding Description
`v2Routes` builds `authv2` as `r.Group("/v2", auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession))`, which only asserts the request has *some* valid user (any role) attached to the gin context via `auth.GetAuthenticatedUser` [1](#0-0) . Nearly every sensitive route in this group is further wrapped in a role-check helper such as `auth.RequiresAdminRole`/`auth.RequiresEditRole`/`auth.RequiresRunRole` (see the key-export, key-import, and admin-only routes) [2](#0-1) . However, at the end of the block, `metricRoutes(authv2)` is called with a comment "Debug routes accessible via authentication" and no role wrapper at all [3](#0-2) . `metricRoutes` registers the full `net/http/pprof` handler set (`cmdline`, `profile`, `trace`, `heap`, `goroutine`, `allocs`, `block`, `mutex`, `threadcreate`) directly under this unguarded group [4](#0-3) . Separately, `debugRoutes` exposes `/debug/vars` (Go `expvar`) gated only by `auth.Authenticate(..., auth.AuthenticateBySession)` with no role check either [5](#0-4) .

The `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` helpers show the intended privilege model: `view` < `run` < `edit` < `admin` [6](#0-5) . Because pprof/debug endpoints skip all of these checks, a session or API token with only the `view` role (the lowest privilege, granted to read-only operators) can pull `pprof.Cmdline` (process argv, which can include secrets passed via CLI flags) and `pprof.Handler("heap")`/`"goroutine"` dumps (raw process memory/goroutine stacks that can contain decrypted private keys, session tokens, or DB credentials held in memory).

`cookies.FindSessionCookie` itself is unrelated to this authorization gap — it is only referenced from `core/cmd/shell.go` (CLI-side cookie persistence) and a test file, not from the server middleware that resolves role/session on incoming requests [7](#0-6) . The premise that supplying multiple `clsession` cookies bypasses role checks is not supported by the code: `AuthenticateBySession` reads the session ID via the gin session store (`sessions.Default(c).Get(SessionIDKey)`), not by manually scanning `r.Cookies()` for duplicates [8](#0-7) .

### Impact Explanation
An attacker holding only a low-privilege authenticated session or API token (e.g., `view` role, normally read-only) can pivot to reading process command-line arguments and heap/goroutine memory dumps via pprof, potentially exposing node secrets, key material held in memory, or credentials passed as CLI flags — a real, scoped privilege-escalation/secret-disclosure issue matching a "server credential/key theft" impact class. This is not, however, reachable by a fully unauthenticated client as the question's title implies; it requires possession of some valid credential (any role).

### Likelihood Explanation
Precondition: attacker must already hold a valid session cookie or API token for the target node with any role, including the lowest `view` role (or a run-role external-initiator-derived session, since `AuthenticateExternalInitiator` also assigns `UserRoleRun`) [9](#0-8) . Given that credential, exploitation is trivial and repeatable — a simple authenticated `GET /v2/debug/pprof/heap` or `/cmdline` request succeeds with no further checks.

### Recommendation
Wrap `metricRoutes(authv2)` and the `/debug/vars` route with `auth.RequiresAdminRole` so pprof/expvar debug data is only accessible to admin-role users, consistent with the rest of the sensitive endpoints in `v2Routes`.

### Proof of Concept
1. Handler-level integration test using `core/web` test harness (`cltest.NewApplication`, `web.Router`).
2. Create two users: one `admin`, one `view` (or generate a `view`-role API token).
3. Authenticate as the `view` user (session cookie or API-key/secret headers).
4. Issue `GET /v2/debug/pprof/heap` and `GET /v2/debug/pprof/cmdline` with the `view` credentials.
5. Assert current (vulnerable) behavior: HTTP 200 with pprof binary/text payload returned, proving lack of role enforcement.
6. After applying the fix (wrapping with `auth.RequiresAdminRole`), re-run same requests and assert HTTP 403 for the `view` session, and HTTP 200 only for the `admin` session.

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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L312-320)
```go
		authv2.POST("/keys/csa/import", auth.RequiresAdminRole(csakc.Import))
		authv2.POST("/keys/csa/export/:ID", auth.RequiresAdminRole(csakc.Export))

		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/router.go (L445-447)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
	}
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

**File:** core/web/auth/auth.go (L145-150)
```go
	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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

**File:** core/web/cookies.go (L7-16)
```go
// FindSessionCookie returns the cookie with the "clsession" name
func FindSessionCookie(cookies []*http.Cookie) *http.Cookie {
	for _, c := range cookies {
		if c.Name == "clsession" {
			return c
		}
	}

	return nil
}
```
