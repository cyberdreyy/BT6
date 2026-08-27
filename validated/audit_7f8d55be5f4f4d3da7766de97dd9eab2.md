### Title
Missing role check on `/v2/debug/pprof/*` and `/v2/debug/vars` routes allows any authenticated (view-role) user to dump process memory, goroutines and command line - ([File: core/web/router.go])

### Summary
`metricRoutes` and `debugRoutes` register Go's `net/http/pprof` handlers and `expvar` under the same `authv2`/`api` route groups that host all other `/v2` chain-scoped endpoints, but unlike every other sensitive `/v2` handler these debug routes are wrapped only with `auth.Authenticate(...)` and never with `auth.RequiresAdminRole` or any role check. Any authenticated session/token holder - including a user whose role is the lowest ('view') - can therefore hit `/v2/debug/pprof/cmdline`, `/heap`, `/goroutine`, `/trace`, `/profile`, and `/debug/vars`.

### Finding Description
In `core/web/router.go`, `v2Routes` builds `authv2` with only an authentication requirement: [1](#0-0) 
Every privileged handler in that group is explicitly wrapped with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole` (e.g. key export/import, user management, chain/node writes), but the debug routes registered at the end of the group are not: [2](#0-1) [3](#0-2) 

`metricRoutes(authv2)` mounts the raw `net/http/pprof` handlers (`cmdline`, `profile`, `trace`, `heap`, `goroutine`, `allocs`, `block`, `mutex`, `threadcreate`, and the index) directly under the group that already passed the `auth.Authenticate` middleware but performs no role check at all. Similarly, `debugRoutes` mounts `/debug/vars` (Go `expvar`) behind session authentication only, again with no role gating: [4](#0-3) 

Because `auth.Authenticate` only verifies that the caller has a valid session/token - it does not check the user's assigned role - any user account created with the lowest 'view' role (the minimal privilege tier used for read-only dashboards) can successfully call these debug endpoints. `pprof.Cmdline` returns the full process command line (which can include secrets passed as flags, e.g. key-store passwords), and `pprof.Handler("heap")`/`"goroutine"` return raw memory/goroutine dumps that can contain in-memory private keys, passwords, or other secrets currently held by the node process.

Note: the `getChain`/evmChainID mechanism in `core/web/common.go` itself has no connection to these debug routes - chain-scoped `/v2` controllers that call `getChain` (e.g. chains/nodes/transfers controllers) are separate handlers with their own role wrappers. The actual root cause is that the debug/pprof/metrics routes are wired into the same `authv2` group without any role wrapper, not that the evmChainID parameter is used to reach `pprof`.

### Impact Explanation
An authenticated low-privilege ('view' role) user can extract heap/goroutine dumps and process command-line arguments from a running Chainlink node, potentially exposing blockchain private keys held in memory, KMS/keystore passwords passed via CLI flags, or other node secrets. This matches the "server credential/key theft" impact class.

### Likelihood Explanation
The only precondition is possession of any valid authenticated session or API token - even one provisioned with the lowest 'view' role, which is routinely granted to non-privileged dashboard users. No admin, host, or DB access is needed, and the routes are always mounted whenever `NewRouter` is built, making this trivially and repeatably exploitable.

### Recommendation
Wrap `metricRoutes` and `debugRoutes` (or each pprof/expvar handler) with `auth.RequiresAdminRole` (the same standard applied to key export/import and user management endpoints) so that only admin-role sessions can reach `/debug/pprof/*` and `/debug/vars`. Consider disabling these endpoints entirely in production builds or exposing them only on a separate internal-only listener.

### Proof of Concept
1. Handler-level integration test using `httptest`:
   - Build the router via `NewRouter` with a test `chainlink.Application`.
   - Create two users: one with role `view`, one unauthenticated (no session/token).
   - For the `view`-role user, authenticate via `/sessions` to obtain a session cookie (or mint an API token) with role `view`.
   - Issue `GET /v2/debug/pprof/cmdline`, `GET /v2/debug/pprof/heap`, `GET /v2/debug/pprof/goroutine`, `GET /v2/debug/vars` using the `view`-role credentials.
   - Assert: current code returns HTTP 200 with pprof/expvar payload (vulnerability confirmed) instead of the expected HTTP 403 that would be returned if `auth.RequiresAdminRole` were applied (as it is on comparable endpoints like `POST /v2/keys/eth/export/:address`).
2. Add a companion test with `admin`-role credentials to confirm the endpoint still works for the intended privilege level, verifying the fix requires `RequiresAdminRole` gating rather than removing the routes outright.

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

**File:** core/web/router.go (L441-447)
```go
		vault := VaultController{app}
		authv2.POST("/vault/dkg_results/verify", auth.RequiresEditRole(vault.VerifyDKGResult))
		authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))

		// Debug routes accessible via authentication
		metricRoutes(authv2)
	}
```
