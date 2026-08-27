### Title
Unauthenticated access to `/plugins/:name/debug/pprof/*` and `/plugins/:name/metrics` while `/debug/vars` and `/v2/debug/pprof/*` require session/token auth - ([File: core/web/router.go])

### Summary
`loopRoutes` registers `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` directly on the top-level `api` group with no `auth.Authenticate` wrapper, unlike `/debug/vars` (wrapped by `auth.Authenticate(..., auth.AuthenticateBySession)`) and the standard `/v2/debug/pprof/*` routes (wrapped inside `authv2`, requiring token/session auth). Any unauthenticated network client that can reach the node's HTTP listener can pull LOOP plugin heap dumps, goroutine stacks, CPU profiles, and metrics.

### Finding Description
In `core/web/router.go`, `NewRouter` builds `api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))` — this group only rate-limits and loads a session cookie accessor; it does **not** enforce authentication. `debugRoutes(app, api)` at [1](#0-0)  creates a nested `/debug` group wrapped with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, so `GET /debug/vars` requires a valid session.

By contrast, `loopRoutes(app, api)` registers plugin routes directly on the unauthenticated `api` group with plain `gin.HandlerFunc`s and no auth middleware at all: [2](#0-1) . `pluginMetricHandler` (core/web/loop_registry.go:96-128) and `pluginPPROFHandler` (core/web/loop_registry.go:150-166) proxy requests to the internal LOOP plugin's `/metrics` and `/debug/pprof/*` endpoints and stream the response body back to the caller, with only a plugin-name existence check (`l.registry.Get(pluginName)`), not an auth check.

This is inconsistent with the equivalent standard-library pprof routes mounted under `authv2` via `metricRoutes(authv2)` at [3](#0-2) , which are protected by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (`v2Routes`, lines 245-248). So the same class of sensitive debug/pprof data is authenticated on one path (`/v2/debug/pprof/*`, `/debug/vars`) and completely open on another (`/plugins/:name/debug/pprof/*`, `/plugins/:name/metrics`).

### Impact Explanation
An unauthenticated attacker with network access to the node's web server can retrieve `pprof` heap/goroutine/CPU profiles and Prometheus metrics for any running LOOP plugin by simply guessing/enumerating a plugin name (plugin names are generally known/predictable, e.g. `median`, `mercury`, etc.) and requesting `GET /plugins/<name>/debug/pprof/heap` or `/plugins/<name>/metrics`. Heap and goroutine dumps can leak in-memory secrets (API keys, private key material, internal state) and internal topology, and profiling/metrics endpoints can be used for reconnaissance or resource-exhaustion (30s+ CPU profile capture on demand). This matches the "information disclosure of sensitive data" bounty class.

### Likelihood Explanation
No credentials or role are required — this is reachable by any unauthenticated network client that can send HTTP requests to the node's API port, provided LOOP plugins are configured (LOOPP/plugin architecture in use). The request is trivially repeatable and requires only knowledge/guessing of a plugin name; no session cookie, API token, or EI credential is needed.

### Recommendation
Wrap the routes registered in `loopRoutes` with the same authentication middleware used for other debug/pprof endpoints, e.g. mount them under a group with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (matching `authv2`/`/debug` group semantics), or at minimum require `auth.AuthenticateBySession` consistent with `/debug/vars`, before merging plugin metrics/pprof proxy handlers into the router.

### Proof of Concept
Handler-level integration test using `httptest`:
1. Build the router via `NewRouter` (or a minimal gin engine mirroring `debugRoutes` + `loopRoutes`) with a test `chainlink.Application` and a `plugins.LoopRegistry` containing a registered plugin `x` pointing at a local `httptest.Server` that serves `net/http/pprof` handlers.
2. `req1 := httptest.NewRequest("GET", "/debug/vars", nil)` with no session cookie → serve via router → assert `resp.Code == http.StatusUnauthorized`.
3. `req2 := httptest.NewRequest("GET", "/plugins/x/debug/pprof/heap", nil)` with no session cookie/token → serve via router → assert `resp.Code != http.StatusUnauthorized` (expect 200 or the proxied plugin response, not 401), demonstrating that plugin debug/pprof data is returned without authentication while `/debug/vars` requires it.
4. Optionally repeat with `/plugins/x/metrics` to show the same bypass for `pluginMetricHandler`.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L230-236)
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```
