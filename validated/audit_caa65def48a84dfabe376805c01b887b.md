### Title
Unauthenticated plugin pprof symbol resolution (and full pprof proxy) via `/plugins/:name/debug/pprof/*` - ([File: core/web/loop_registry.go])

### Summary
The LOOP plugin pprof proxy routes registered in `loopRoutes` are mounted directly on the unauthenticated `api` router group, unlike the node's own `/debug/pprof/*` and `/v2/debug/pprof/*` routes which are wrapped in `auth.Authenticate`. This lets any unauthenticated client POST to `/plugins/:name/debug/pprof/symbol` (or GET `/plugins/:name/debug/pprof/*profile`) to have the node proxy the request to the internal LOOP plugin's pprof endpoint and return the raw response, including symbol table/address data.

### Finding Description
In `core/web/router.go`, `NewRouter` mounts `debugRoutes`, `sessionRoutes`, `v2Routes`, and `loopRoutes` all on the same `api` group, which only carries rate limiting and session middleware, no authentication: [1](#0-0) 

`debugRoutes` explicitly wraps its group with `auth.Authenticate(...)`: [2](#0-1) 

and the equivalent standard-library pprof endpoints (`metricRoutes`) are only registered inside the `authv2` group, which requires `auth.AuthenticateByToken`/`auth.AuthenticateBySession`: [3](#0-2) [4](#0-3) 

However, `loopRoutes` registers its handlers directly on `r` (the bare `api` group) with no auth middleware at all: [5](#0-4) 

`pluginPPROFPOSTSymbolHandler` reads the unauthenticated caller's request body and proxies it verbatim as a POST to the plugin's internal `/debug/pprof/symbol` endpoint, returning the plugin's response (the resolved symbol table) directly to the caller: [6](#0-5) 

`pluginPPROFHandler` similarly proxies GET requests to any `/debug/pprof/*` sub-path (index, cmdline, profile, trace, heap, goroutine, etc.) of the plugin's internal pprof server: [7](#0-6) 

Since there is no `auth.Authenticate` (or any role check) in the chain for these routes, any network client that can reach the node's HTTP API (no session cookie, no API token, no EI credentials) can hit these endpoints as long as at least one LOOP plugin is registered in `l.registry`.

### Impact Explanation
This falls under internal binary/internals disclosure: an unauthenticated party can retrieve plugin binary symbol-to-address mappings and full pprof profiling data (heap, goroutine stacks, CPU profile, execution trace) from any running LOOP plugin process. This aids memory-layout/ASLR-bypass reconnaissance and can leak sensitive operational data embedded in goroutine stacks/heap dumps (e.g., in-flight request data, potentially key material references), materially lowering the bar for further remote exploitation of the plugin process.

### Likelihood Explanation
No credentials, roles, or preconditions are required beyond network reachability to the node's web server and at least one LOOP plugin being registered in `plugins.LoopRegistry` (common in production nodes using LOOP-based relayers/plugins). The request is a single unauthenticated HTTP POST/GET, fully repeatable, and requires no special timing or race condition.

### Recommendation
Wrap `loopRoutes`' pprof-related routes (`pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`, and ideally `pluginMetricHandler`/`discoveryHandler` if they expose sensitive info) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for `/v2/debug/pprof/*`, and consider gating them behind `auth.RequiresAdminRole` given the sensitivity of pprof/debug data.

### Proof of Concept
Go handler-level integration test in `core/web`:
1. Build a `chainlink.Application` test app with a `plugins.LoopRegistry` containing one registered plugin (`Name: "test"`, with `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` that serves a fake `/debug/pprof/symbol` response).
2. Call `web.NewRouter(app, nil)` to get the real `*gin.Engine`.
3. Issue `httptest.NewRequest("POST", "/plugins/test/debug/pprof/symbol", body)` with no `Authorization` header and no session cookie, run it through `engine.ServeHTTP`.
4. Assert: expected `http.StatusUnauthorized` (matching behavior of `/v2/debug/pprof/*`), but actual observed result is `http.StatusOK` with the fake plugin's symbol-table body returned verbatim — demonstrating the missing `auth.Authenticate` wrapper on `loopRoutes` in `core/web/router.go`.

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

**File:** core/web/router.go (L245-249)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
```

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** core/web/loop_registry.go (L168-188)
```go
func (l *LoopRegistryServer) pluginPPROFPOSTSymbolHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/symbol", l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	body, err := io.ReadAll(gc.Request.Body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error reading plugin pprof request body: %s", err))
		return
	}
	l.doRequest(gc, "POST", pluginURL, bytes.NewReader(body), timeout, pluginName)
}
```
