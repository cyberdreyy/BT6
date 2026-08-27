### Title
Unauthenticated access to LOOP plugin pprof debug endpoints allows heap/goroutine dump disclosure - (File: core/web/loop_registry.go, core/web/router.go)

### Summary
The `/plugins/:name/debug/pprof/*profile` and `/plugins/:name/debug/pprof/symbol` routes registered by `loopRoutes` are mounted directly on the base `api` router group with no authentication middleware, unlike the node's own `/debug/pprof` routes which are wrapped in `authv2` (`metricRoutes(authv2)`) requiring token or session authentication. Any unauthenticated network client can therefore pull heap, goroutine, profile, and other pprof dumps from any registered LOOP plugin process.

### Finding Description
In `core/web/router.go`, `NewRouter` calls `loopRoutes(app, api)` at [1](#0-0)  where `api` is only wrapped with rate limiting and session middleware — no `auth.Authenticate*` wrapper. Inside `loopRoutes`, the pprof endpoint is registered without any auth middleware: [2](#0-1) .

By contrast, the node's own pprof endpoints registered via `metricRoutes` are deliberately placed inside the `authv2` group which enforces `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` before any admin/edit/run-role checks: [3](#0-2)  and [4](#0-3) .

The handler `pluginPPROFHandler` in `core/web/loop_registry.go` takes the plugin name and the wildcard profile path from the URL, resolves the plugin from the registry, and proxies the request straight through to the plugin's internal `/debug/pprof/<profile>` endpoint with no authorization check of its own: [5](#0-4) . The response body from the plugin (heap dump, goroutine stack, CPU profile, etc.) is returned verbatim to the caller via `doRequest`: [6](#0-5) .

Plugin names needed to construct the URL are discoverable via the also-unauthenticated `/discovery` endpoint: [7](#0-6) , which lists all registered LOOP plugins: [8](#0-7) .

No authentication, session cookie, or API token check exists anywhere on this path from the HTTP request into `pluginPPROFHandler`, so an unauthenticated network client can fetch memory/goroutine dumps from any LOOP plugin (e.g., Median, VRF, or other relayer/reporting plugins) that may contain in-memory secrets, RPC URLs, chain-specific keys, or other sensitive process state.

### Impact Explanation
This is an authentication bypass on a debug endpoint that exposes internal process memory of a plugin process (heap/goroutine/profile dumps), potentially disclosing key material, in-flight report data, or configuration/secrets held in memory by the LOOP plugin — matching the "sensitive data / key material disclosure" bounty impact class. Because pprof heap dumps are essentially process memory snapshots, this is more severe than a typical information-leak endpoint.

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's HTTP API and knowledge of a plugin name, which is itself obtainable unauthenticated via `/discovery`. The attack is a single unauthenticated GET request, fully repeatable, and works regardless of the caller's role (no session, no API token needed).

### Recommendation
Wrap the `loopRoutes` plugin pprof/metric endpoints (or at minimum the pprof ones) with the same `auth.Authenticate(...)` (and ideally `auth.RequiresAdminRole`) middleware used for `metricRoutes(authv2)`, so that `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` require authenticated admin access just like the node's own `/debug/pprof` group.

### Proof of Concept
1. Start a mock HTTP server simulating a LOOP plugin, serving `/debug/pprof/heap` with a fixed byte payload, and register it in a fake `plugins.LoopRegistry` with `EnvCfg.PrometheusPort` pointing at the mock server's port.
2. Build the gin router via `NewRouter` (or directly call `loopRoutes(app, api)` on a fresh `gin.Engine`/`RouterGroup`) with the fake app/registry, without setting any `Authorization` header or session cookie.
3. Issue `httptest` GET request to `/plugins/<name>/debug/pprof/heap`.
4. Assert response status is `200 OK` and body equals the mock plugin's heap dump payload, proving disclosure without authentication.
5. As a control, issue GET to `/v2/debug/pprof/heap` (node's own pprof, under `authv2`) without credentials and assert `401 Unauthorized`, demonstrating the asymmetry between the two supposedly equivalent debug surfaces.

### Citations

**File:** core/web/router.go (L87-92)
```go
	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)

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

**File:** core/web/router.go (L445-446)
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
