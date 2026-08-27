### Title
Unauthenticated LOOP plugin pprof/debug endpoint discloses process memory and stack data - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
`loopRoutes` registers `/plugins/:name/debug/pprof/*profile` and `/plugins/:name/debug/pprof/symbol` directly on the unauthenticated `api` group, while the functionally equivalent `/debug/vars` route is explicitly wrapped in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, and the node's own `net/http/pprof` routes (`metricRoutes`) are only mounted inside the authenticated `authv2` group. This asymmetry lets any unauthenticated caller pull heap dumps, goroutine stacks, and other pprof profiles from a LOOP plugin process without any credentials.

### Finding Description
In `core/web/router.go`, `NewRouter` builds an `api` group with only rate limiting and session middleware (no auth) and calls `loopRoutes(app, api)` directly: [1](#0-0) 

`loopRoutes` registers plugin pprof endpoints with zero authentication middleware: [2](#0-1) 

Compare this to `debugRoutes`, which wraps `/debug/vars` in session authentication: [3](#0-2) 

And `metricRoutes` (the node's own `net/http/pprof` handlers), which is only invoked inside the authenticated `authv2` group: [4](#0-3) 

The handler `LoopRegistryServer.pluginPPROFHandler` (in `core/web/loop_registry.go`) takes the unauthenticated `:name` and `*profile` path params and proxies the request to the plugin's internal pprof port, returning the raw response body to the caller: [5](#0-4) 

An unauthenticated attacker can first enumerate registered plugin names via the also-unauthenticated `/discovery` endpoint (registered in the same `loopRoutes` function without auth): [6](#0-5) [7](#0-6) 

then request `GET /plugins/<name>/debug/pprof/heap` (or `/goroutine`, `/profile`, etc.) to receive a full memory/goroutine dump of the plugin process with no authentication check anywhere in the request path — `doRequest` simply forwards and returns the body: [8](#0-7) 

### Impact Explanation
Heap and goroutine dumps from a LOOP plugin process can contain sensitive in-memory data (e.g., decrypted key material, credentials, RPC secrets, or internal state) processed by relayer/median/OCR plugin processes. This matches the Chainlink bounty "sensitive data/key disclosure via unauthorized access" impact class — an unauthenticated actor obtains debug-level introspection into a node subcomponent that the codebase itself treats as sensitive (evidenced by gating the analogous `/debug/vars` and `/debug/pprof/*` node routes behind session/token auth).

### Likelihood Explanation
No credentials, roles, or tokens are required — a plain HTTP GET request from any network-reachable client suffices. The plugin name can be discovered via the equally unauthenticated `/discovery` endpoint, making the attack fully self-contained and repeatable against any node with LOOP plugins registered and the web server reachable.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/debug/pprof/*profile` and `/plugins/:name/debug/pprof/symbol` routes) in the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (and/or token) middleware used for `debugRoutes` and `metricRoutes`, and consider gating it with an admin-only role check (`auth.RequiresAdminRole`) consistent with the sensitivity of pprof/debug data.

### Proof of Concept
Go handler-level integration test plan (in `core/web` test package, following the pattern of existing router tests):
1. Build a test app via `cltest.NewApplication`/`setupWebServerRouter`-style helper (as used in existing `TestRouter_*` tests) with a `LoopRegistry` containing a registered fake plugin (e.g. name `"median"`) whose `EnvCfg.PrometheusPort` points to a local `httptest.Server` serving `/debug/pprof/heap`.
2. Start the router via `NewRouter` with no session/token in the request.
3. `GET /debug/vars` with no `Authorization`/session cookie → assert response status `401 Unauthorized`.
4. `GET /plugins/median/debug/pprof/heap` with no `Authorization`/session cookie → assert response status `200 OK` and non-empty body matching the fake pprof server's response.
5. Assert the inconsistency: same test run demonstrates `/debug/vars` blocked while `/plugins/:name/debug/pprof/heap` is open, confirming the authorization boundary violation described above.

### Citations

**File:** core/web/router.go (L86-91)
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

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
