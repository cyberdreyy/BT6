### Title
Unauthenticated disclosure of LOOP plugin Prometheus metrics via `/plugins/:name/metrics` - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `/discovery` and `/plugins/:name/metrics` (backed by `LoopRegistryServer.pluginMetricHandler`) directly on the base `api` router group without any `auth.Authenticate` middleware, unlike every other functional route group (`v2Routes`, `sessionRoutes`, `debugRoutes`) which explicitly wrap handlers with authentication. Any unauthenticated network client can enumerate plugin names via `/discovery` and then fetch raw Prometheus metrics for that plugin.

### Finding Description
In `core/web/router.go`, `NewRouter` calls `loopRoutes(app, api)` on the shared `api` group, which only carries generic rate-limiting and session middleware — no authentication requirement is attached to this call: [1](#0-0) .

Inside `loopRoutes`, both `/discovery` and `/plugins/:name/metrics` (and the pprof forwarding routes) are registered with no `auth.Authenticate(...)` wrapper, in stark contrast to `v2Routes` which explicitly builds an `authv2` group using `auth.Authenticate(app.AuthenticationProvider(), ...)` for every sensitive endpoint: [2](#0-1) .

`pluginMetricHandler` looks up the plugin by name from the registry and proxies a GET request to the plugin's internal Prometheus port, returning the raw response body verbatim to the caller with no authentication check performed inside the handler itself: [3](#0-2) .

The `discoveryHandler` further leaks the exact metrics path and plugin name for every registered LOOP plugin, providing the attacker with the plugin name precondition mentioned in the question: [4](#0-3) .

This confirms the reachable, unauthenticated path: `GET /discovery` → enumerate plugin names → `GET /plugins/<name>/metrics` → 200 OK with raw plugin metrics, with no session cookie, API token, or EI credential required. The same lack-of-auth issue also applies to the pprof forwarding routes (`/plugins/:name/debug/pprof/*profile` and `.../symbol`), which is a related but distinct exposure.

### Impact Explanation
This results in disclosure of internal LOOP plugin state (Prometheus metrics, which can include labeled identifiers such as chain IDs, job IDs, contract addresses, or other operational/internal identifiers depending on the plugin) to any unauthenticated network caller who can reach the node's web server port. This matches the Chainlink bounty "information disclosure of internal/sensitive node state" impact class — it is not a fund-movement or job-execution bypass, but it does violate the authentication soundness invariant that protects internal operational data, and combined with the unauthenticated pprof endpoints, could aid further reconnaissance or facilitate resource-exhaustion style requests (e.g., `seconds` param on CPU profile).

### Likelihood Explanation
No credentials are required at all — any network client that can reach the node's configured web server port can exploit this. The only precondition is knowledge of a plugin name, which is trivially obtained from the also-unauthenticated `/discovery` endpoint. This is fully reproducible and repeatable with a simple `curl` or Go `net/http` client.

### Recommendation
Wrap the `loopRoutes` registrations with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used elsewhere (or a dedicated internal-only auth mechanism/network binding), so `/discovery`, `/plugins/:name/metrics`, and the pprof forwarding routes require authentication before being served on the node's public API surface. Alternatively, bind these routes to a separate internal-only listener not exposed alongside the authenticated API.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/loop_registry_test.go`):
1. Build a `gin.Engine` via `NewRouter(app, nil)` (or directly call `loopRoutes(app, group)` on a fresh `gin.RouterGroup`) with a mocked `chainlink.Application` whose `GetLoopRegistry()` returns a `*plugins.LoopRegistry` containing one registered fake plugin (e.g., name `"median"`) with an `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` that serves fake Prometheus text output.
2. Issue `httptest.NewRequest("GET", "/discovery", nil)` through the engine with no `Authorization` header or session cookie; assert `http.StatusOK` and that the JSON body contains the plugin name/path.
3. Issue `httptest.NewRequest("GET", "/plugins/median/metrics", nil)` with no auth headers/cookies through the same engine; assert `http.StatusOK` (not `401`) and that the response body equals the fake plugin's metrics text, confirming unauthenticated disclosure.
4. As a control, repeat step 3 against `/v2/jobs` (an `authv2`-protected route) and assert `http.StatusUnauthorized`, demonstrating the inconsistency between `loopRoutes` and the rest of the router.

### Citations

**File:** core/web/router.go (L87-91)
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

**File:** core/web/loop_registry.go (L53-81)
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

	b, err := l.jsonMarshalFn(groups)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, err = w.Write([]byte(err.Error()))
		if err != nil {
			l.logger.Error(err)
		}
		return
	}
	_, err = w.Write(b)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		l.logger.Error(err)
	}
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
