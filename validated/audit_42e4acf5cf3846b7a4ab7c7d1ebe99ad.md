Confirmed: `loopRoutes` is registered on the top-level `api` group without any `auth.Authenticate*` middleware, unlike every other sensitive route group in `router.go` (e.g., `authv2`, `debugRoutes`). [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unauthenticated LOOP plugin name enumeration via differential status codes on GET /plugins/:name/metrics - (File: core/web/loop_registry.go)

### Summary
The `/plugins/:name/metrics` route (and its sibling `/discovery`, `/plugins/:name/debug/pprof/*` routes) is registered without any authentication middleware in `loopRoutes`, unlike `authv2`/`debugRoutes` groups. An unauthenticated attacker can send GET requests with different `:name` values and distinguish registered vs unregistered LOOP plugins via the 200/500 vs 404 response difference from `pluginMetricHandler`'s `registry.Get` lookup.

### Finding Description
`NewRouter` mounts `loopRoutes(app, api)` on the bare `api` group, which only carries rate-limiting and session middleware — no `auth.Authenticate(...)` wrapper is applied, in contrast to `debugRoutes`, `sessionRoutes` (DELETE), and all of `v2Routes`' `authv2`/`userOrEI` groups which explicitly require token/session/EI authentication. [4](#0-3) [1](#0-0) 

In `pluginMetricHandler`, the plugin name from the URL path parameter is looked up in `l.registry.Get(pluginName)`; if not found, it returns HTTP 404 with a body echoing the plugin name; if found, it proceeds to attempt forwarding to an internal metrics endpoint (returning 200 on success or 500 on a connection failure). [5](#0-4)  This creates a clear, unauthenticated oracle: 404 means the plugin name is not registered; any non-404 response (200 or 500) means it is registered. No authentication, session, or token check exists on this path to prevent an anonymous caller from probing arbitrary `:name` values.

### Impact Explanation
This allows unauthenticated enumeration of which LOOP (Local Out Of Process) plugins are registered/running on the node (e.g., specific chain relayer plugins such as solana, starknet, etc., or custom plugin names), which is internal operational/infrastructure information disclosure about the node's configuration. This maps to a low/informational disclosure impact class — it does not by itself expose secrets, keys, or allow privilege escalation or fund movement, since the actual metrics payload returned is Prometheus metrics data proxied from the internal LOOP process, not credentials, and the route is otherwise intended to be scraped by an internal Prometheus (the discoverable target itself lists plugin names via `/discovery`, which is also unauthenticated, compounding this). The scoped, provable impact here is limited to registered-plugin-name enumeration via response-code differences.

### Likelihood Explanation
Trivial and fully unauthenticated: the attacker needs only network access to the node's API/web server, no credentials, no role, no token. It is a single GET request repeated with different `:name` guesses; fully repeatable and requires no special conditions.

### Recommendation
Wrap `loopRoutes` (or at minimum `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*`, `/plugins/:name/debug/pprof/symbol`) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` similar to `authv2`, or restrict these routes to loopback/internal network only, since the comment in `pluginMetricHandler` states "this endpoint is internal btw the node and plugin" — the routing intent conflicts with its current unauthenticated exposure.

### Proof of Concept
Handler-level integration test using `httptest`/gin test engine:
1. Build a `gin.Engine` calling `loopRoutes(app, group)` with a mock `chainlink.Application` whose `GetLoopRegistry()` returns a `*plugins.LoopRegistry` pre-populated with one registered plugin, e.g. name `"solana"`.
2. Issue `GET /plugins/solana/metrics` with no `Authorization` header or session cookie; assert response is not 404 (200 if underlying mock LOOP metrics server is stubbed, or 500 if connection fails) — i.e., assert `!= http.StatusNotFound`.
3. Issue `GET /plugins/doesnotexist/metrics` with no auth; assert response is exactly `http.StatusNotFound` and body contains `plugin "doesnotexist" does not exist`.
4. Assert the differential (`res1.Code != http.StatusNotFound && res2.Code == http.StatusNotFound`) to prove the enumeration oracle exists without any authentication middleware in the request chain.

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
