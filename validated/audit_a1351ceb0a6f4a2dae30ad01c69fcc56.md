### Title
pprof/debug routes registered without role check are reachable by any authenticated user, including view-role API-token holders - ([File: core/web/router.go])

### Summary
The `/v2/debug/pprof/*` routes registered by `metricRoutes(authv2)` in `v2Routes` are mounted under the `authv2` group, which only requires `auth.AuthenticateByToken` or `auth.AuthenticateBySession` to succeed - it does not apply `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or any role gate at all. Since `AuthenticateByToken`/`AuthenticateBySession` accept any valid user regardless of their `UserRole` (`view`, `run`, `edit`, `admin`), a user holding only a low-privilege `view`-role API access-key/secret pair can reach `cmdline`, `heap`, `goroutine`, `profile`, `trace`, etc.

### Finding Description
In `core/web/router.go`, the `authv2` group is created with:
```go
authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
    auth.AuthenticateByToken,
    auth.AuthenticateBySession,
))
``` [1](#0-0) 

Most sensitive handlers registered on `authv2` are explicitly wrapped with `auth.RequiresAdminRole` or `auth.RequiresEditRole` (e.g. user management, transfers). However, `metricRoutes(authv2)` is invoked directly with the comment "Debug routes accessible via authentication" and no role wrapper at all:
```go
// Debug routes accessible via authentication
metricRoutes(authv2)
``` [2](#0-1) 

`metricRoutes` registers the full `net/http/pprof` handler set with no additional authorization check:
```go
func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	...
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	...
}
``` [3](#0-2) 

`auth.AuthenticateByToken` validates only that the API access-key/secret pair matches a stored user; it never inspects `user.Role` before setting `SessionUserKey`:
```go
user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
...
ok, err := clsessions.AuthenticateUserByToken(token, &user)
...
c.Set(SessionUserKey, &user)
``` [4](#0-3) 

`auth.Authenticate` itself is role-agnostic - it only checks whether one of the given `authMethod`s succeeds:
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
		...
		c.Next()
	}
}
``` [5](#0-4) 

Role enforcement in this codebase is opt-in per-route via `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole`, each of which walks `UserRole` (`view` < `run` < `edit` < `admin`):
```go
const (
	UserRoleAdmin UserRole = "admin"
	UserRoleEdit  UserRole = "edit"
	UserRoleRun   UserRole = "run"
	UserRoleView  UserRole = "view"
)
``` [6](#0-5) 

Because `metricRoutes(authv2)` does not wrap its handlers in any `Requires*Role` function, the only guard is generic authentication success — any credential holder of any role (including `view`) passes. This means the debug/pprof group is reachable by the lowest-privilege API credential, exposing:
- `/v2/debug/pprof/cmdline` — full process command line, which can include secrets passed via CLI flags.
- `/v2/debug/pprof/heap` — full heap dump, which can contain in-memory key material, tokens, and passwords.
- `/v2/debug/pprof/goroutine`, `/v2/debug/pprof/trace`, `/v2/debug/pprof/profile` — goroutine stacks and CPU/execution traces that can leak internal state, addresses, and function arguments.

Regarding the specific `AuthenticateExternalInitiator` target named in the question: that method is only wired into a *separate* route group (`userOrEI`), which registers only `/v2/ping` and `/v2/jobs/:ID/runs`:
```go
userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
	auth.AuthenticateExternalInitiator,
	auth.AuthenticateByToken,
	auth.AuthenticateBySession,
))
userOrEI.GET("/ping", ping.Show)
userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
``` [7](#0-6) 

The pprof/metric debug routes are not registered on `userOrEI`, so an external-initiator credential (which is auto-elevated to `UserRoleRun` inside `AuthenticateExternalInitiator`, see `core/web/auth/auth.go` lines 145-148) cannot reach them through that specific authenticator path. The exploitable path instead runs through the `authv2` group and `auth.AuthenticateByToken`/`AuthenticateBySession`, not through `AuthenticateExternalInitiator`.

### Impact Explanation
A holder of a restricted, view-role API access-key/secret pair (the minimal privilege level the system defines) can retrieve process heap dumps, goroutine stacks, and the full command line of the running Chainlink node process via `/v2/debug/pprof/*`. If secrets (e.g., a keystore password, database URL with credentials, or vault secrets) were ever passed as CLI flags or are resident in memory, they become retrievable by a low-privileged authenticated user. This matches the "Critical - server credential/key theft" impact class since it can expose node secrets from a running node to any authenticated user, not just admins.

### Likelihood Explanation
Exploitation requires only a valid API access-key/secret pair with any role (the lowest, `view`, suffices) — no admin, edit, or run privilege is required, and no external-initiator flow is involved despite `AuthenticateExternalInitiator` being the entity named in the question. This is trivially and repeatably reachable: any provisioned "view" user or read-only API key can issue a `GET /v2/debug/pprof/heap` or `/v2/debug/pprof/cmdline` request and receive the response, since neither `metricRoutes` nor the `authv2` group applies a `Requires*Role` check.

### Recommendation
Wrap `metricRoutes(authv2)` (and ideally `debugRoutes`'s `/debug/vars`) with `auth.RequiresAdminRole`, or move the pprof route registrations to their own group protected explicitly by `auth.RequiresAdminRole`, consistent with the rest of the sensitive `authv2` endpoints (`uc.Index`, `ets.Create`, etc.).

### Proof of Concept
1. In a handler/integration test (in the style of `core/web/router_test.go`), start the application via `cltest.NewApplicationEVMDisabled(t)` and create a user with `UserRoleView` using the app's user ORM (see patterns in `core/sessions/user.go`/existing tests that call `NewUser`).
2. Issue an API token for that view-role user (equivalent to `POST /v2/user/token` flow) to obtain an `X-API-KEY`/`X-API-SECRET` pair.
3. Send `GET /v2/debug/pprof/cmdline` and `GET /v2/debug/pprof/heap` using `app.NewHTTPClient` configured with the view-role token headers.
4. Assert `resp.StatusCode == http.StatusOK` (not `403 Forbidden`), and that the response body contains process command-line/heap content — demonstrating the absence of a `RequiresAdminRole`/`RequiresEditRole` gate on these routes, contrasted with a similarly-constructed request to `POST /v2/users` (an admin-gated route) which should return `403`.

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

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
}
```

**File:** core/web/auth/auth.go (L93-109)
```go
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)
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

**File:** core/sessions/user.go (L29-34)
```go
const (
	UserRoleAdmin UserRole = "admin"
	UserRoleEdit  UserRole = "edit"
	UserRoleRun   UserRole = "run"
	UserRoleView  UserRole = "view"
)
```
