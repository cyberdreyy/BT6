### Title
pprof debug routes mounted under `authv2` without any role gate, allowing view-role users to extract heap/goroutine memory dumps - ([File: core/web/router.go])

### Summary
`metricRoutes` mounts `net/http/pprof` handlers (`/v2/debug/pprof/*`) inside the `authv2` group at `core/web/router.go` line 446, but unlike every other sensitive endpoint in that group, none of the pprof routes are wrapped with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole`. Any authenticated user — including one with the minimum `view` role — can reach `/heap`, `/goroutine`, `/profile`, etc.

### Finding Description
`authv2` is a `gin.RouterGroup` protected only by `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)` [1](#0-0) , which merely establishes that *some* valid user is present, without any role check. Individual routes in this group are then expected to layer on a role wrapper (`RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole`) as seen throughout the file, e.g. lines 251-254, 276-282, 298-302, 312-383. However `metricRoutes(authv2)` at line 446 registers the pprof handlers directly with no such wrapper: [2](#0-1) .

`auth.Authenticate` only sets `SessionUserKey` in the gin context after successful auth; it performs no role comparison at all — that logic lives exclusively in `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` in `core/web/auth/auth.go` (lines 200-255). Since pprof handlers bypass these wrapper functions entirely, `GetAuthenticatedUser`'s role (`view`, `run`, `edit`, or `admin`) is never inspected before serving `/v2/debug/pprof/heap`, `/goroutine`, `/profile`, `/trace`, etc.

Attack flow: an attacker holding only a `view`-role API token (or session) sends `GET /v2/debug/pprof/heap` with valid `X-API-KEY`/`X-API-SECRET` headers (or a valid session cookie). `auth.AuthenticateByToken`/`AuthenticateBySession` succeeds and sets the view-role user in context; because there is no role check on this route, the request proceeds straight to `pprof.Handler("heap").ServeHTTP`, and the full process heap dump is returned to the attacker.

### Impact Explanation
Heap/goroutine/profile dumps from a live Chainlink node process can contain in-memory decrypted secrets — private keys (ETH/OCR/P2P/VRF/CSA), API tokens, database credentials, or other sensitive material transiently held in memory — depending on what is resident at capture time. This corresponds to Chainlink's "sensitive information disclosure" bounty class; a `view`-role account (the lowest privilege tier, intended only for read-only dashboard access) should never be able to obtain raw process memory. This is a role/authorization-gate omission distinct from a misconfiguration, since it's a code-level gap in `metricRoutes`/`v2Routes`, not an operator setting.

### Likelihood Explanation
- Preconditions: attacker needs any valid authenticated credential of the lowest role (`view`), obtainable via a legitimately-issued read-only API token or a compromised low-privilege session — no admin/edit/run privilege required.
- Feasibility: single unauthenticated-of-role HTTP GET request; fully repeatable, deterministic, no race conditions or timing dependency.
- The vulnerability is directly evidenced by the absence of any `auth.RequiresXRole` call surrounding `metricRoutes(authv2)` at [3](#0-2) , in contrast to every other route registered in the same block.

### Recommendation
Wrap the pprof routes with at least `auth.RequiresAdminRole` (consistent with the sensitivity of raw memory/heap access), e.g., change `metricRoutes(authv2)` to accept the group and apply the role wrapper per route, or apply the wrapper as a group-level middleware:
```go
pprofGroup := r.Group("/debug/pprof", auth.RequiresAdminRoleMiddleware()) // or wrap each handler with auth.RequiresAdminRole
```
ensuring every `pprofGroup.GET/POST` call in `metricRoutes` (`core/web/router.go` lines 186-198) is gated the same way `/log` PATCH, `/keys/*` mutation routes, etc. are gated elsewhere in `v2Routes`.

### Proof of Concept
Add to the existing RBAC route-map test suite (`core/web/router_test.go` or wherever `TestRBAC_Routemap_*` lives):
1. Build a table entry for each pprof path: `/v2/debug/pprof/`, `/cmdline`, `/profile`, `/symbol`, `/trace`, `/allocs`, `/block`, `/goroutine`, `/heap`, `/mutex`, `/threadcreate`.
2. For each, spin up a test app/router via `setupWebTestApp` (or the equivalent helper used by other RBAC tests) and issue a `GET`/`POST` request authenticated as a `view`-role user (`clsessions.UserRoleView`).
3. Assert `resp.Code == http.StatusForbidden` (matching the `RequiresAdminRole` behavior at `core/web/auth/auth.go` lines 247-251) or `http.StatusUnauthorized`.
4. Run against current code to confirm the test fails (returns `200 OK` with pprof payload), demonstrating the gap; after applying the recommended fix, confirm it returns `403 Forbidden`.

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

**File:** core/web/router.go (L446-446)
```go
		metricRoutes(authv2)
```
