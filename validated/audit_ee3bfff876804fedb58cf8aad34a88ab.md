### Title
Unauthenticated LOOP plugin registry disclosure via GET /discovery - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the base `api` router group with no `auth.Authenticate` middleware, unlike every other route group in `NewRouter` (`debugRoutes`, `sessionRoutes`, `v2Routes`). This lets any unauthenticated client enumerate registered LOOP plugin names via `discoveryHandler`.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the base `api` group with only rate-limiting and session middleware (no auth), then calls `loopRoutes(app, api)` at [1](#0-0) . Inside `loopRoutes`, the routes are registered without any `auth.Authenticate(...)` wrapper: [2](#0-1) . Compare this to `debugRoutes`, `sessionRoutes`, and `v2Routes`, which all explicitly wrap their groups with `auth.Authenticate(app.AuthenticationProvider(), ...)` before registering handlers (e.g. [3](#0-2) , [4](#0-3) ).

`discoveryHandler` in `core/web/loop_registry.go` iterates `l.registry.List()` (a `*plugins.LoopRegistry`) and returns Prometheus service-discovery JSON containing each registered plugin's name via `LabelMetaPluginName` and target address: [5](#0-4) . `RegisteredLoop.Name` corresponds directly to the plugin/service identifier registered via `LoopRegistry.Register` [6](#0-5) . Since no auth middleware guards this route, an attacker with a plain HTTP client can send `GET /discovery` with no cookies/headers and receive this data with a 200 response.

Additionally the sibling routes `/plugins/:name/metrics` and the pprof-forwarding endpoints (`pluginMetricHandler`, `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`) share this same unauthenticated exposure, since they are registered in the same unguarded group [7](#0-6) , though the question scopes specifically to `/discovery`.

### Impact Explanation
An unauthenticated attacker can enumerate the internal LOOP plugin registry (plugin names and internal discovery hostname/port), which falls under information disclosure of internal node topology/plugin architecture. This matches a low/medium "informational disclosure" bounty class rather than direct fund loss or key compromise, since the discovery endpoint itself does not expose secrets, private keys, or job data — but it does reveal internal plugin names/addresses that are otherwise meant to be internal-only and could aid further reconnaissance/attack planning (e.g., targeting `/plugins/:name/metrics` or pprof endpoints, which are also unauthenticated).

### Likelihood Explanation
No preconditions or credentials are required — the route is reachable by any network client that can reach the node's web server, with a single unauthenticated GET request. This is fully reproducible and repeatable at will.

### Recommendation
Wrap `loopRoutes` registration with the standard `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware (and an appropriate role gate, e.g. `auth.RequiresAdminRole` or `auth.RequiresViewRole`), consistent with `debugRoutes`/`v2Routes`, before exposing `/discovery`, `/plugins/:name/metrics`, and the pprof forwarding endpoints.

### Proof of Concept
Go handler-level test plan:
1. Build an `app` via existing test helpers (`cltest.NewApplication` or similar used in `core/web/loop_registry_internal_test.go`), register a fake LOOP plugin in `app.GetLoopRegistry()`.
2. Call `web.NewRouter(app, nil)` to obtain the `*gin.Engine`.
3. Use `httptest.NewRecorder()` and `httptest.NewRequest(http.MethodGet, "/discovery", nil)` — do NOT set any `Authorization` header or session cookie.
4. Call `engine.ServeHTTP(w, req)`.
5. Assert `w.Code == http.StatusOK` (current, vulnerable behavior) instead of the expected `401`/`403`.
6. Assert the response body (JSON array of `targetgroup.Group`) contains the registered plugin's name under the `__meta_plugin_name` label, confirming registry disclosure without authentication.

### Citations

**File:** core/web/router.go (L87-92)
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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/loop_registry.go (L52-65)
```go
// discoveryHandler implements service discovery of prom endpoints for LOOPs in the registry
func (l *LoopRegistryServer) discoveryHandler(w http.ResponseWriter, req *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	groups := make([]*targetgroup.Group, 0, 1+len(l.registry.List()))

	// add node metrics to service discovery
	groups = append(groups, pluginGroup(l.discoveryHostName, l.exposedPromPort, "/metrics"))

	// add all the plugins
	for _, registeredPlugin := range l.registry.List() {
		group := pluginGroup(l.discoveryHostName, l.exposedPromPort, pluginMetricPath(registeredPlugin.Name))
		group.Labels[LabelMetaPluginName] = model.LabelValue(registeredPlugin.Name)
		groups = append(groups, group)
	}
```

**File:** plugins/loop_registry.go (L22-25)
```go
type RegisteredLoop struct {
	Name   string
	EnvCfg loop.EnvConfig
}
```
