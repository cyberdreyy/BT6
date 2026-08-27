### Title
Unauthenticated LOOP plugin discovery and metrics/pprof proxy endpoints allow full plugin enumeration and internal debug data disclosure - ([File: core/web/router.go])

### Summary
The `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` routes registered by `loopRoutes` in `core/web/router.go` are mounted directly on the base `api` group with no `auth.Authenticate` middleware and no role wrapper, unlike every other sensitive route in the router. An unauthenticated attacker can call `/discovery` to enumerate all registered LOOP plugin names, then use each name to pull Prometheus metrics and full `net/http/pprof` debug output (including `heap`, `goroutine`, `profile`, `trace`) from internal LOOP processes.

### Finding Description
`loopRoutes` is called from `NewRouter` at [1](#0-0)  and registers its routes on `api`, the same base group used for `debugRoutes`/`sessionRoutes` before any per-route auth is attached: [2](#0-1) 

Contrast this with every other endpoint touching similar data: `debugRoutes` wraps `/debug/vars` in `auth.Authenticate(..., auth.AuthenticateBySession)` [3](#0-2) , and the equivalent standard-library pprof routes registered via `metricRoutes` are only mounted inside the authenticated `authv2` group at [4](#0-3) . `loopRoutes`, by contrast, has zero `auth.Authenticate*` call and zero `auth.Requires*Role` wrapper.

In `core/web/loop_registry.go`, `discoveryHandler` iterates `l.registry.List()` and returns a JSON body containing every registered plugin's name and its derived metrics path (`/plugins/<name>/metrics`) with no credential check: [5](#0-4) [6](#0-5) . `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` then take the attacker-supplied `:name` path param, look it up via `l.registry.Get(pluginName)`, and proxy the request straight through to the internal LOOP process's `/metrics` or `/debug/pprof/*` endpoint, returning the raw response body to the caller with no authentication or authorization check performed anywhere in the handler chain: [7](#0-6) [8](#0-7) .

Because these routes sit on `api` (which only has rate limiting and session middleware attached, not authentication), any unauthenticated HTTP client that can reach the node's web port can walk `/discovery` → enumerate plugin names → hit `/plugins/:name/metrics` and `/plugins/:name/debug/pprof/*` (including `heap`, `goroutine`, `trace`, `profile`, `cmdline`) for every LOOP plugin, and none of view/run/edit/admin role checks are ever evaluated for this code path.

### Impact Explanation
This is an information-disclosure / authorization-bypass finding: unauthenticated attackers can enumerate internal service topology (`/discovery`) and pull live pprof debug data (heap dumps, goroutine stacks, CPU profiles) and Prometheus metrics from LOOP plugin processes (e.g., median, mercury, or other relayer LOOPs) without any credential. Heap/goroutine dumps and metrics can leak internal state, configuration details, or operational data that should require at minimum a "view" role, matching the Chainlink bounty class of authentication/authorization bypass and unintended information disclosure. It does not directly return private keys or move funds, but it materially violates the intended role-authorization model (view/run/edit/admin is enforced everywhere else in the router but not here).

### Likelihood Explanation
Fully unauthenticated and trivially repeatable — the attacker needs only network access to the node's HTTP web server (no session, no API token, no EI credential). The `/discovery` response is JSON that directly hands over the exact plugin names needed to construct the follow-up requests, so no guessing or brute force is required.

### Recommendation
Wrap `loopRoutes` (or each of its route registrations) with the same authentication/role middleware used elsewhere, e.g. `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` plus an appropriate `auth.RequiresViewRole`/`auth.RequiresAdminRole` wrapper, consistent with how `metricRoutes(authv2)` and `debugRoutes` are protected, or move these endpoints off the public API port entirely (e.g., bind to an internal-only listener).

### Proof of Concept
Go handler-level integration test plan:
1. Build a test `chainlink.Application` mock (as in `core/internal/mocks/application.go`) whose `GetLoopRegistry()` returns a `*plugins.LoopRegistry` with 2-3 registered plugins (via `registry.Register(...)`), each backed by a fake internal HTTP server exposing `/metrics` and `/debug/pprof/*`.
2. Call `web.NewRouter(app, nil)` to build the full `*gin.Engine`, mirroring `TestRouter` patterns already used in `core/web/loop_registry_test.go`.
3. Issue an unauthenticated `httptest` `GET /discovery` request (no session cookie, no `Authorization` header) and assert `200 OK`; parse the JSON body and extract the `__meta_plugin_name` labels and `__metrics_path__` targets.
4. For each discovered plugin name, issue unauthenticated `GET /plugins/<name>/metrics` and `GET /plugins/<name>/debug/pprof/heap` (and `/goroutine`, `/cmdline`) requests.
5. Assert all responses return `200 OK` (proxied plugin data) rather than `401 Unauthorized`/`403 Forbidden`, in contrast to a control request to `authv2`-protected `/v2/keys/eth` without credentials, which should return `401`.

### Citations

**File:** core/web/router.go (L87-91)
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

**File:** core/web/loop_registry.go (L150-188)
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

**File:** core/web/loop_registry.go (L237-239)
```go
func pluginMetricPath(name string) string {
	return fmt.Sprintf("/plugins/%s/metrics", name)
}
```
