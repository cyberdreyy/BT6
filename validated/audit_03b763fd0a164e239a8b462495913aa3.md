### Title
Unauthenticated plugin pprof/metrics proxy endpoints allow memory/goroutine disclosure - ([File: core/web/router.go])

### Summary
`loopRoutes` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the outer `api` router group with no `auth.Authenticate` middleware, unlike every other sensitive route in the file. Any unauthenticated client can hit these routes and have the node proxy pprof/metrics data from internal LOOP plugin processes back to them.

### Finding Description
In `NewRouter`, the `api` group is created with only rate-limiting and session middleware (no auth check) at [1](#0-0) , and `loopRoutes(app, api)` is called on that same unauthenticated group. Inside `loopRoutes`, the plugin metrics/pprof routes are registered with no auth wrapper at all: [2](#0-1) .

This is in stark contrast to the two other pprof-related route registrations in the same file:
- `debugRoutes` wraps `/debug/vars` in `auth.Authenticate(...)` [3](#0-2) .
- `metricRoutes` (the standard `net/http/pprof` handlers) is only ever called from inside the authenticated `authv2` block: [4](#0-3) .

The handlers themselves (`pluginMetricHandler`, `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler` in `core/web/loop_registry.go`) perform no authentication or authorization check — they only validate that `l.registry.Get(pluginName)` exists, then proxy the raw request to the internal LOOP plugin's pprof/metrics HTTP server and stream the response body verbatim back to the caller: [5](#0-4) [6](#0-5) . The `/debug/pprof/*profile` wildcard even lets the caller choose any pprof sub-endpoint (heap, goroutine, profile, trace, allocs, etc.) supported by the plugin's pprof server, and control query parameters like `seconds`/`debug`/`gc` via `pprofURLVals`: [7](#0-6) .

Because these routes sit on the bare `api` group with no `auth.Authenticate` call anywhere in the chain, a fully anonymous external HTTP client can request them and receive plugin runtime data (goroutine stack traces, heap memory profiles, CPU profiles) without any credential.

### Impact Explanation
This is an unauthenticated information-disclosure vulnerability: an anonymous attacker can obtain heap/goroutine/CPU profiles and raw Prometheus metrics for any registered LOOP plugin (e.g., median/relayer plugins) simply by knowing or guessing a plugin name reachable via `/discovery`. Such data can leak internal addresses, configuration details, function names, and operational metrics useful for further attacks, and constitutes a "confidential data exposure" style finding under Chainlink's bounty impact classes, though it does not directly enable fund movement or key theft.

### Likelihood Explanation
No credential, role, or special network position is required — any client that can reach the node's HTTP API port can enumerate plugin names via the equally unauthenticated `/discovery` endpoint and then query `/plugins/:name/metrics` or `/plugins/:name/debug/pprof/*profile`. This requires only that the node runs in LOOP-plugin mode (a supported/common deployment) and is repeatable at will.

### Recommendation
Wrap `loopRoutes` registrations in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (and an appropriate role check such as `auth.RequiresAdminRole` or `auth.RequiresViewRole`), consistent with how `debugRoutes` and `metricRoutes` are protected. If these endpoints must remain reachable by an external Prometheus scraper without session/API-token auth, protect them with a dedicated bearer-token check similar to `prometheusHandler`'s token comparison.

### Proof of Concept
1. Build a `chainlink.Application` test double (or use existing router test setup in `core/web/router_test.go`) with `app.GetLoopRegistry()` returning a registry containing a fake plugin `"testplugin"` with a `PrometheusPort` pointing to a local `httptest.Server` that serves a fixed pprof-style body (e.g., contains a fake goroutine stack string).
2. Call `web.NewRouter(app, nil)` to build the full `*gin.Engine`, wrap in `httptest.NewServer`.
3. Send unauthenticated `GET /plugins/testplugin/debug/pprof/heap` and `GET /plugins/testplugin/metrics` with no `Authorization` header and no session cookie.
4. Assert: response status is `200 OK` (not `401 Unauthorized`), and response body contains the fake sensitive marker content forwarded from the plugin.
5. Contrast with `GET /debug/vars` and `GET /v2/keys/eth` (assert these correctly return `401`), showing that the plugin pprof/metrics routes are the outlier lacking auth enforcement.

### Citations

**File:** core/web/router.go (L77-91)
```go
	rl := config.WebServer().RateLimit()
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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** core/web/loop_registry.go (L132-148)
```go
func pprofURLVals(gc *gin.Context) (urlVals url.Values, timeout time.Duration) {
	urlVals = make(url.Values)
	if db, ok := gc.GetQuery("debug"); ok {
		urlVals.Set("debug", db)
	}
	if gc, ok := gc.GetQuery("gc"); ok {
		urlVals.Set("gc", gc)
	}
	timeout = PPROFOverheadSeconds * time.Second
	if sec, ok := gc.GetQuery("seconds"); ok {
		urlVals.Set("seconds", sec)
		if i, err := strconv.Atoi(sec); err == nil {
			timeout = time.Duration(i+PPROFOverheadSeconds) * time.Second
		}
	}
	return urlVals, timeout
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
