### Title
Unauthenticated exposure of LOOP plugin pprof profiling endpoints - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
The route `/plugins/:name/debug/pprof/*profile` is registered in `loopRoutes()` without any authentication middleware, unlike every other debug/pprof-related route in the router. This lets an unauthenticated caller trigger `LoopRegistryServer.pluginPPROFHandler`, which proxies the request straight through to the internal LOOP plugin's pprof HTTP server and returns the raw response to the caller.

### Finding Description
In `core/web/router.go`, `loopRoutes()` registers:
```go
r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
``` [1](#0-0) 

These are added directly to the top-level `api` group (`loopRoutes(app, api)` in `NewRouter`), which only carries the generic authenticated-rate-limit and session middleware — no `auth.Authenticate(...)` wrapper. [2](#0-1) 

Compare this to the node's own pprof endpoints registered via `metricRoutes(authv2)`, which are nested under `authv2`, a group protected by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`: [3](#0-2) [4](#0-3) 

and to `debugRoutes`, which explicitly wraps `/debug/vars` with session authentication: [5](#0-4) 

`pluginPPROFHandler` itself does no additional authorization check — it only validates the plugin name exists in the registry, then forwards the request to the internal plugin's pprof port and streams the response body back verbatim: [6](#0-5) [7](#0-6) 

An attacker only needs a valid plugin name, which is itself discoverable via the also-unauthenticated `/discovery` endpoint (`r.GET("/discovery", ...)`) that lists all registered plugins and their metrics paths: [8](#0-7) [9](#0-8) 

With that, an unauthenticated GET to `/plugins/<name>/debug/pprof/heap`, `/profile`, or `/goroutine` returns the plugin's live pprof capture with no credentials required.

### Impact Explanation
This is an unauthenticated information disclosure of internal runtime state (goroutine stacks, heap contents, potentially secrets/keys held in memory by the LOOP plugin process) and a free CPU/DoS vector, since `/profile` and `/trace`-style captures accept a `seconds` parameter controlling capture duration (up to `PPROFOverheadSeconds` + requested seconds), consuming plugin CPU on each unauthenticated call. This matches a "sensitive information disclosure via unauthenticated debug endpoint" bounty class rather than direct fund loss, but is a real, concretely exploitable node-security issue distinct from misconfiguration since the missing auth wrapper is a coding omission relative to sibling routes.

### Likelihood Explanation
No credentials or role are required at all — a bare unauthenticated HTTP client can enumerate plugin names via `/discovery` and then call the pprof endpoint. This is trivially repeatable and requires only network reachability to the node's web server, which is the same reachability required for the (also unauthenticated) `/sessions` login endpoint. The general per-IP `Authenticated` rate limiter still applies (`rl.AuthenticatedPeriod()/rl.Authenticated()` at the `api` group level) but does not require any authentication token.

### Recommendation
Wrap the LOOP plugin pprof/metrics routes with the same authentication requirement used for the node's own `/debug/pprof` routes and `/debug/vars` — e.g., register them under `authv2`/`api` group protected by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (and consider `auth.RequiresAdminRole` given the sensitivity of raw memory/stack disclosure), instead of the unauthenticated `api` group in `loopRoutes()`.

### Proof of Concept
1. In a handler-level integration test (similar to existing tests in `core/web/loop_registry_internal_test.go`), start a `gin.Engine` built via `NewRouter` (or directly call `loopRoutes(app, api)`) with a mock LOOP plugin registered in `plugins.LoopRegistry`, and a stub HTTP server on `loopHostName:PrometheusPort` serving a dummy `/debug/pprof/heap` payload.
2. Issue `GET /plugins/<pluginName>/debug/pprof/heap` with **no** `Authorization` header/session cookie.
3. Assert the response status is `200 OK` and the body equals the stub plugin's pprof payload, proving the request reached `pluginPPROFHandler` and was proxied without any authentication check.
4. As a control, issue `GET /debug/vars` (wrapped by `auth.Authenticate`) with no credentials and assert `401/403`, contrasting the two routes' auth posture.

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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L53-65)
```go
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

**File:** core/web/loop_registry.go (L190-215)
```go
func (l *LoopRegistryServer) doRequest(gc *gin.Context, method, url string, body io.Reader, timeout time.Duration, pluginName string) {
	ctx, cancel := context.WithTimeout(gc.Request.Context(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin pprof request: %s", err))
		return
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		msg := "plugin pprof handler failed to post plugin url " + html.EscapeString(url)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q pprof", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
}
```
