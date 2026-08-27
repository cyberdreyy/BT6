### Title
Unauthenticated LOOP plugin pprof endpoints leak process memory/goroutine dumps - ([File: core/web/router.go])

### Summary
`loopRoutes` is mounted directly on the base `api` gin group in `NewRouter`, which only has rate-limiting and session middleware attached, with no authentication wrapper. This exposes `GET /plugins/:name/debug/pprof/*profile` and the related metrics/symbol endpoints to any unauthenticated caller, unlike the sibling `/debug/vars` route which is explicitly wrapped in `auth.Authenticate(...)`.

### Finding Description
In `core/web/router.go`, the `api` group is created with only rate limiting and cookie sessions middleware: [1](#0-0) 
`debugRoutes` explicitly creates a nested `/debug` group wrapped with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` before exposing `expvar`: [2](#0-1) 
However, `loopRoutes` registers its routes directly on the `api` group with no such auth wrapper: [3](#0-2) 
This means `GET /plugins/:name/debug/pprof/*profile` is dispatched straight to `LoopRegistryServer.pluginPPROFHandler`, defined in `core/web/loop_registry.go`, which looks up the named LOOP plugin and proxies the pprof request to the plugin's internal HTTP endpoint, returning the raw profile bytes to the caller: [4](#0-3) 
There is no session/token authentication check anywhere in this handler chain or in `loopRoutes` before it reaches the handler, so any unauthenticated network client that can reach the node's web server can request heap, goroutine, CPU, or allocs profiles for any registered LOOP plugin by name.

### Impact Explanation
An unauthenticated attacker can pull heap/goroutine/CPU profiles from any LOOPP (LOOP plugin) process running alongside the node (e.g., relayer/median/mercury plugins). These profiles can contain sensitive in-memory data such as key material, job configuration, or other internal state, constituting a credential/secret disclosure vulnerability reachable without any authentication.

### Likelihood Explanation
No preconditions or credentials are required — this is reachable by any unauthenticated client that can send an HTTP request to the node's web server. The plugin name and profile type (`heap`, `goroutine`, `profile`, etc.) are simply URL path parameters, so exploitation is trivial and repeatable, e.g., `GET /plugins/mockLoopImpl/debug/pprof/heap?seconds=5`.

### Recommendation
Wrap `loopRoutes` (and any other pprof/metrics endpoints containing sensitive data) in an authenticated group, mirroring `debugRoutes`, e.g. register them under `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))` (and consider `auth.RequiresAdminRole` given the sensitivity of raw memory dumps) before exposing `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`, and `pluginMetricHandler`.

### Proof of Concept
1. Build a `gin.Engine` via `web.NewRouter` (or use existing test harness in `core/web` tests) with a `LoopRegistry` containing a registered test plugin (`plugins.NewTestLoopRegistry`).
2. Send `GET /plugins/mockLoopImpl/debug/pprof/heap?seconds=5` with no `Authorization` header and no session cookie.
3. Assert the response status is not `401 Unauthorized` and instead proxies/returns pprof data (status `200` or a proxy error indicating the handler was invoked), demonstrating the handler is reachable without authentication.
4. Contrast with `GET /debug/vars` under identical unauthenticated conditions, which should return `401` due to `auth.Authenticate` in `debugRoutes`, confirming the inconsistency and the missing auth on `loopRoutes`.

### Citations

**File:** core/web/router.go (L78-91)
```go
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)

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

**File:** core/web/loop_registry.go (L150-166)
```go
func (l *LoopRegistryServer) pluginPPROFHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/"+gc.Param("profile"), l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	l.logger.Infow("Forwarding plugin pprof request", "plugin", pluginName, "url", pluginURL)
	l.doRequest(gc, "GET", pluginURL, nil, timeout, pluginName)
}
```
