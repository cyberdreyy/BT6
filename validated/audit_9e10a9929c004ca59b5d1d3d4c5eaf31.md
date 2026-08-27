### Title
Unauthenticated pprof symbol/profile endpoints exposed via `loopRoutes` allow blind memory-address probing - ([File: core/web/router.go])

### Summary
`loopRoutes` registers `/plugins/:name/debug/pprof/*profile` (GET) and `/plugins/:name/debug/pprof/symbol` (POST) directly on the public `api` router group with no `auth.Authenticate` wrapper, unlike every other sensitive route in this file. This lets any unauthenticated client resolve memory-address-to-symbol mappings and pull full pprof profiles/traces from a registered LOOPP plugin process.

### Finding Description
In `NewRouter`, `loopRoutes(app, api)` is called on the bare `api` group [1](#0-0) . Inside `loopRoutes`, the discovery, metric, and pprof handlers are all registered without any `auth.Authenticate(...)` middleware: [2](#0-1) .

This is inconsistent with the rest of the codebase's design intent: standard Go `net/http/pprof` endpoints exposed via `metricRoutes` are explicitly placed behind the authenticated `authv2` group with the comment "Debug routes accessible via authentication" [3](#0-2) , and `/debug/vars` is likewise wrapped with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [4](#0-3) .

`pluginPPROFPOSTSymbolHandler` reads the plugin name from the URL, looks it up in `l.registry`, and if found proxies the raw request body to the plugin's internal `/debug/pprof/symbol` endpoint, returning the response body verbatim to the caller: [5](#0-4) . The GET pprof handler (`profile`, `trace`, `heap`, etc.) behaves the same way: [6](#0-5) .

An attacker does not even need to guess the plugin name — the unauthenticated `GET /discovery` endpoint enumerates all registered plugin names via `l.registry.List()`: [7](#0-6) .

Exploit flow: (1) `GET /discovery` (no auth) to enumerate plugin names; (2) `POST /plugins/{name}/debug/pprof/symbol` (no auth) with a body of hex addresses to resolve them to Go runtime symbol names in the plugin process, or `GET /plugins/{name}/debug/pprof/profile|trace|heap` to pull raw memory/CPU profiles. None of these hit any `auth.Authenticate` middleware, so the request reaches the handler and gets proxied straight through, returning attacker-controlled introspection of the plugin's live memory/symbol layout.

### Impact Explanation
This is an authentication-soundness gap: sensitive runtime introspection (pprof symbol resolution, heap/goroutine/profile/trace dumps) for LOOPP plugin processes (e.g. median/mercury/OCR2 relayer plugins) is reachable by any unauthenticated network client. This falls under Chainlink's "sensitive data exposure without authentication" / info-disclosure bounty class — it does not itself move funds or forge signatures, but it materially aids exploitation of any other memory-corruption or ASLR-dependent bug in the plugin binary, and can leak internal addresses, goroutine stacks, and potentially secret material resident in the plugin's heap.

### Likelihood Explanation
No credentials, no role, and no special network position are required — a plain unauthenticated HTTP client on the API port can immediately enumerate plugin names via `/discovery` and hit `/plugins/:name/debug/pprof/symbol` or the GET pprof paths. This is fully repeatable and deterministic, contingent only on at least one LOOPP plugin being registered in `l.registry` (true whenever the node runs any plugin-based relayer, e.g. Solana/Starknet/median LOOPPs).

### Recommendation
Wrap `loopRoutes(app, api)` (or at minimum the `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used elsewhere, and consider requiring `auth.RequiresAdminRole` given the sensitivity of raw memory/profile dumps, consistent with how `metricRoutes(authv2)` is protected.

### Proof of Concept
Go handler-level integration test plan:
1. Build the router via `NewRouter` with a test `chainlink.Application` whose `GetLoopRegistry()` returns a registry containing one fake plugin (`"testplugin"`) pointing `EnvCfg.PrometheusPort` at a local `httptest.Server` that responds to `/debug/pprof/symbol` with a canned symbol resolution body.
2. Issue `httptest.NewRequest("POST", "/plugins/testplugin/debug/pprof/symbol", strings.NewReader("0x1234"))` with **no** `Authorization` header and **no** session cookie, and call `engine.ServeHTTP(w, req)`.
3. Assert `w.Code == http.StatusOK` and `w.Body` contains the canned symbol data from the fake plugin server, proving the request reached `pluginPPROFPOSTSymbolHandler` and was proxied without any 401/403 from `auth.Authenticate`.
4. Repeat for `GET /plugins/testplugin/debug/pprof/heap` and `GET /discovery` to confirm the entire `loopRoutes` group is unauthenticated, contrasted with a control request to `GET /v2/jobs` (under `authv2`) which should return `401 Unauthorized` without credentials.

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
