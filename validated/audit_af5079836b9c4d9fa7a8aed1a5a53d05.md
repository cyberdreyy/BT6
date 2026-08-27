### Title
Unauthenticated disclosure of per-plugin Prometheus metrics via GET /plugins/:name/metrics - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `/plugins/:name/metrics` (and the sibling `/discovery` and pprof plugin routes) directly on the base `api` route group, which only has rate-limiting and session middleware applied — no `auth.Authenticate` wrapper is present, unlike every other sensitive route group in the router (`debugRoutes`, `sessionRoutes` auth group, `authv2`). Any unauthenticated client can hit this endpoint and receive the raw Prometheus metrics scraped from the named LOOP plugin process.

### Finding Description
`NewRouter` builds the base `api` group with only rate limiting and session-cookie middleware [1](#0-0) , then calls `loopRoutes(app, api)` without any auth wrapper, registering `GET /plugins/:name/metrics` bound to `loopRegistry.pluginMetricHandler` [2](#0-1) .

By contrast, comparable operational endpoints are explicitly protected: `debugRoutes` wraps `/debug/vars` in `auth.Authenticate(..., auth.AuthenticateBySession)` [3](#0-2) , and the `pprof` debug routes registered via `metricRoutes` are only mounted under the authenticated `authv2` group [4](#0-3) . `loopRoutes` has no such protection.

`pluginMetricHandler` looks up the plugin by name from the in-process `LoopRegistry`, proxies a GET request to the plugin's local metrics port, and returns the raw response body verbatim with no redaction [5](#0-4) . Prometheus metrics for Chainlink LOOP plugins (e.g., median, VRF, etc.) commonly expose operational counters such as job/report counts, error rates, latency histograms, chain/contract addresses used as labels, and other internal state — none of which is meant for public disclosure. Since no credential is required to reach this handler, any unauthenticated network client can request `GET /plugins/<name>/metrics` and receive this data.

### Impact Explanation
This is an unauthenticated internal telemetry/information disclosure: an outside party can enumerate plugin names (via the equally unauthenticated `/discovery` endpoint [6](#0-5) ) and then read live plugin metrics, revealing job counts, error rates, and possibly address/chain identifiers used as metric labels. This falls under "sensitive information disclosure" rather than fund movement or credential theft, but it does expose internal operational details that should require authentication like all other admin/observability endpoints in this router.

### Likelihood Explanation
No preconditions or credentials are needed — a bare unauthenticated GET request from any network-reachable client is sufficient, and the request is fully repeatable at will. The only prerequisite is network-level reachability to the node's web server port, which is the same reachability required for every other route in this file.

### Recommendation
Wrap `loopRoutes` (or at minimum the metrics/pprof plugin routes) in the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for `authv2`/`metricRoutes`, or move these routes into the authenticated `authv2` group, consistent with how `/debug/vars` and `/debug/pprof/*` are already protected.

### Proof of Concept
Go handler-level integration test plan:
1. Build the router via `NewRouter` with a test `chainlink.Application` whose `LoopRegistry` has a registered test plugin (e.g., using `plugins.NewTestLoopRegistry`).
2. Issue `httptest.NewRequest("GET", "/plugins/<name>/metrics", nil)` with no `Authorization` header and no session cookie.
3. Serve it through the router and assert the response status is `200 OK` (not `401 Unauthorized`) and the body contains proxied metrics text.
4. Contrast with a request to `/debug/vars` under identical unauthenticated conditions, asserting it correctly returns `401`/redirect, to demonstrate the inconsistency in auth enforcement between the two operational endpoints.

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
