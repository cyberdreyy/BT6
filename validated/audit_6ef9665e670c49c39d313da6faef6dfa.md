### Title
Unauthenticated exposure of LOOP plugin registry, live pprof profiles, and plugin metrics via `loopRoutes` - ([File: core/web/router.go])

### Summary
`loopRoutes` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the top-level `api` group with no `auth.Authenticate` wrapper, unlike every other sensitive route group in `NewRouter` (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any unauthenticated caller can reach these handlers, causing disclosure of the plugin registry, live pprof profiles/heap dumps, and internal plugin metrics.

### Finding Description
In `core/web/router.go`, `NewRouter` builds `api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))` and calls `loopRoutes(app, api)` at line 91 alongside `debugRoutes`, `healthRoutes`, `sessionRoutes`, and `v2Routes`. Every other function that registers sensitive endpoints on `api` explicitly wraps its sub-group with `auth.Authenticate(...)`: [1](#0-0) [2](#0-1) 

`loopRoutes`, however, registers its four routes directly on the passed-in `r` (`api`) group with zero authentication middleware: [3](#0-2) 

The handlers backing these routes, defined in `core/web/loop_registry.go`, do real work with no additional auth check inside them:
- `discoveryHandler` lists all registered LOOP plugins and their metrics endpoints via `l.registry.List()`. [4](#0-3) 
- `pluginMetricHandler` proxies to the plugin's `/metrics` endpoint and returns the raw response body. [5](#0-4) 
- `pluginPPROFHandler` proxies arbitrary `/debug/pprof/<profile>` paths (including `heap`, `profile`, `goroutine`, etc., taken from the `:profile` wildcard param) to the plugin's internal pprof server and streams the raw response back to the caller. [6](#0-5) 
- `pluginPPROFPOSTSymbolHandler` similarly proxies `/debug/pprof/symbol` POST requests. [7](#0-6) 

Because none of these routes pass through `auth.Authenticate`, a caller with no session cookie and no API token can hit `GET /plugins/median/debug/pprof/heap` or `GET /discovery` and get a `200 OK` with live heap/profile data or the plugin registry contents. This contrasts with the equivalent authenticated pprof routes registered via `metricRoutes(authv2)` inside the `v2Routes` function, which are properly gated behind `auth.Authenticate` + role checks. [8](#0-7) 

### Impact Explanation
This matches the "unauthenticated disclosure of internal node data" bounty class: an unauthenticated attacker can dump live process heap memory (`/plugins/:name/debug/pprof/heap`), CPU/goroutine/block profiles, and full symbol tables from any registered LOOP plugin process, as well as enumerate all registered plugins and their internal Prometheus/pprof ports via `/discovery` and `/plugins/:name/metrics`. Heap dumps and profiling data from a Chainlink plugin process can expose sensitive in-memory data (e.g., transient key material, request contents), and the registry/metrics endpoints leak internal topology (ports, plugin names) useful for further reconnaissance and follow-on attacks.

### Likelihood Explanation
No credentials are required — the attacker only needs network reachability to the node's web server. The routes are always registered whenever LOOP plugins exist in the registry, and the requests are simple unauthenticated GET/POST calls, making this trivially and repeatably exploitable by any unprivileged network client.

### Recommendation
Wrap the LOOP plugin routes in an authenticated group, consistent with `debugRoutes`/`metricRoutes` usage elsewhere, e.g.:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
    loopRegistry := NewLoopRegistryServer(app)
    group := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))
    group.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
    group.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
    group.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
    group.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
Additionally consider requiring an admin/run role given the sensitivity of heap/profile data, matching the `RequiresAdminRole`/`RequiresRunRole` pattern used for other debug endpoints.

### Proof of Concept
Go handler-level integration test plan:
1. Construct a test `chainlink.Application` mock with `GetLoopRegistry()` returning a registry containing one registered plugin (e.g., name `"median"`, with a stub `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` that serves `/debug/pprof/heap` and `/metrics`).
2. Call `NewRouter(app, nil)` to build the engine (no auth token, no session cookie configured on the request).
3. Issue `httptest.NewRequest("GET", "/discovery", nil)` through `router.ServeHTTP` with an `httptest.NewRecorder()`, and assert `recorder.Code == http.StatusOK` and the body contains the registered plugin's target group — proving no auth challenge was raised.
4. Issue `httptest.NewRequest("GET", "/plugins/median/debug/pprof/heap", nil)` with no `Authorization`/`Cookie` headers, assert `recorder.Code == http.StatusOK` (not `401`), confirming the pprof proxy returns data to an anonymous caller.
5. As a control, repeat the same request against an authenticated route (e.g., `GET /v2/config`) without credentials and confirm it returns `401 Unauthorized`, demonstrating the asymmetry between `v2Routes`/`debugRoutes` (protected) and `loopRoutes` (unprotected).

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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L444-447)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
	}
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

**File:** core/web/loop_registry.go (L96-128)
```go
func (l *LoopRegistryServer) pluginMetricHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/metrics", l.loopHostName, p.EnvCfg.PrometheusPort)
	req, err := http.NewRequestWithContext(gc.Request.Context(), http.MethodGet, pluginURL, nil)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin metrics request: %s", err))
		return
	}
	res, err := l.promClient.Do(req)
	if err != nil {
		msg := "plugin metric handler failed to get plugin url " + html.EscapeString(pluginURL)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q metrics", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
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
