### Title
View-role authenticated users can retrieve full pprof memory/CPU profiles via unprotected `/v2/debug/pprof/*` routes - ([File: core/web/router.go])

### Summary
`metricRoutes` mounts the standard `net/http/pprof` handlers (`heap`, `profile`, `goroutine`, `trace`, etc.) directly onto the authenticated `authv2` group with no `auth.RequiresAdminRole`/`auth.RequiresEditRole` wrapper, unlike nearly every other sensitive endpoint in `v2Routes`. Because the group is only protected by base `auth.Authenticate`, any authenticated principal — including the lowest-privilege view-role user or a token with view-only permissions — can call these endpoints and obtain a raw heap/CPU profile of the node process.

### Finding Description
`metricRoutes` is defined as: [1](#0-0) 
It registers `pprof.Index`, `pprof.Cmdline`, `pprof.Profile`, `pprof.Symbol`, `pprof.Trace`, and the `heap`/`allocs`/`block`/`goroutine`/`mutex`/`threadcreate` handlers with no per-route role wrapper (`auth.RequiresAdminRole`, `auth.RequiresEditRole`, `auth.RequiresRunRole`) applied to any of them — contrasting with the rest of `v2Routes`, where sensitive actions such as key export/import, deletion, and creation are explicitly wrapped in `auth.RequiresAdminRole` or `auth.RequiresEditRole`: [2](#0-1) 

The `authv2` group itself is only gated by base `auth.Authenticate` with `AuthenticateByToken`/`AuthenticateBySession`, which validates identity but not role: [3](#0-2) 

Since `metricRoutes` receives a `*gin.RouterGroup` and mounts its subgroup at `/debug/pprof` with no additional role middleware, any group passed to it (the `authv2` group per the router wiring) exposes pprof handlers to all authenticated roles, including `view`. A `pprof.Profile`/`pprof.Handler("heap")` response dumps raw process memory/stack data, which can contain private key material, session tokens, and other secrets held in Go heap objects (e.g., ECDSA/EdDSA private keys loaded into memory for signing, API session tokens, database credentials cached in structs).

### Impact Explanation
This is an authorization/role-check gap: the lowest-privilege authenticated role (`view`) can extract full memory and CPU profiles of the node process, which is far beyond what a read-only user is entitled to and creates a path to secret/key material disclosure (matches Chainlink bounty class: "unauthorized access to sensitive data / credential and key material exposure via authorization bypass").

### Likelihood Explanation
Preconditions are minimal: an attacker only needs any valid authenticated session or API token with the `view` role (the lowest privilege, easy to obtain or already possessed by low-trust integrations). No admin/edit/run role, and no additional exploitation steps are required — a single `GET /v2/debug/pprof/heap` (or `/profile`) call succeeds. This is trivially repeatable.

### Recommendation
Wrap all pprof route registrations in `metricRoutes` with `auth.RequiresAdminRole` (consistent with other sensitive introspection/administration endpoints), or move pprof mounting outside the general `authv2` group into a separate admin-only-authenticated group.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a test router via `NewRouter` (as in existing `core/web/router_test.go`-style tests) with a mock `AuthenticationProvider`.
2. Create a session/token with `sessions.RoleView` (or equivalent view-only credential).
3. Issue `GET /v2/debug/pprof/heap` with the view-role credential attached (cookie/session or `Authorization` header, matching `AuthenticateByToken`/`AuthenticateBySession`).
4. Assert HTTP 200 and that the response body is a valid pprof profile (non-empty, `application/octet-stream` content, parseable by `google.golang.org/protobuf` pprof parser), confirming no admin-role check blocked the view-role request.
5. Repeat for `/v2/debug/pprof/profile` (CPU profile) with a short duration to keep the test fast, and assert 200 with view-role credentials.
6. As a control, verify that other endpoints (e.g., `/v2/keys/eth/import`) reject the same view-role credential with 401/403 due to `auth.RequiresAdminRole`, to demonstrate the inconsistency/gap specifically for pprof routes.

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

**File:** core/web/router.go (L315-320)
```go
		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```
