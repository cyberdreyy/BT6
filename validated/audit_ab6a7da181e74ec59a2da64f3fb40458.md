### Title
Unauthenticated access to LOOP plugin pprof/metrics forwarding endpoints - ([File: core/web/router.go, core/web/loop_registry.go])

### Summary
The `loopRoutes` function registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the root `api` group without any authentication middleware, unlike every other debug/metrics route in the router which is wrapped in `auth.Authenticate(...)`. This lets any unauthenticated client forward arbitrary GET/POST pprof requests to an internal plugin process through the node.

### Finding Description
`NewRouter` mounts `loopRoutes(app, api)` on the bare `api` group [1](#0-0) , with no `auth.Authenticate` wrapper, in contrast to `debugRoutes`, which explicitly wraps `/debug/vars` in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [2](#0-1) , and `metricRoutes`, which is only invoked inside the already-authenticated `authv2` group [3](#0-2) .

`loopRoutes` itself registers the plugin endpoints with no auth handler at all: [4](#0-3) 

`pluginPPROFPOSTSymbolHandler` looks up the plugin by name, reads the raw unauthenticated request body, and forwards it verbatim as a POST to the plugin's internal `/debug/pprof/symbol` endpoint, then relays the raw response back to the caller: [5](#0-4) 

The shared `doRequest` helper performs the actual HTTP call to `loopHostName`/`PrometheusPort` and writes the plugin's response body directly back to the unauthenticated caller: [6](#0-5) 

The sibling GET handler `pluginPPROFHandler` forwards to any `*profile` path under `/debug/pprof/`, including `/debug/pprof/profile` (CPU profiling) and `/debug/pprof/trace`, again with zero authentication: [7](#0-6) 

Because none of these routes pass through `auth.Authenticate`, `auth.RequiresEditRole`, `auth.RequiresAdminRole`, or session/token checks, any network client that can reach the node's HTTP API can invoke them without credentials.

### Impact Explanation
An unauthenticated attacker can use the node as an open proxy into the internal LOOP plugin process's pprof HTTP server. This enables: unauthenticated symbol-table probing of the plugin binary (information disclosure of internal function addresses/names), and via the GET route, unauthenticated triggering of CPU/goroutine/heap profiling or blocking `trace`/`profile` requests against the plugin process, which can degrade or hang the plugin and thus the node (denial of service). It also allows internal-network request smuggling/probing against whatever is listening on `loopHostName:PrometheusPort`, since the destination URL and method are attacker-influenced only by path/query, not validated beyond existence of a registered plugin name. This falls under the bounty categories of authentication bypass on a debug/internal-service surface, leading to information disclosure and node availability impact.

### Likelihood Explanation
No credentials, tokens, or session cookies are required — a bare HTTP request to the node's public/API listener is sufficient. The only precondition is that at least one LOOP plugin is registered in `LoopRegistry` (common for chains using the LOOPP plugin architecture, e.g., Solana/Starknet/Cosmos relayers), which is standard operational configuration, not a misconfiguration. The exploit is trivially repeatable with a single curl/HTTP request.

### Recommendation
Wrap `loopRoutes` registration in the same authentication middleware used elsewhere (e.g., `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`), or at minimum require an admin/edit role for the pprof forwarding endpoints, consistent with `metricRoutes`/`debugRoutes`. Additionally consider restricting `/discovery` and `/plugins/*` behind an internal-only listener rather than the public API group.

### Proof of Concept
Go handler-level integration test plan (using `httptest` + the router built by `web.NewRouter`):
1. Build a test `chainlink.Application` with a `LoopRegistry` containing one registered plugin (e.g., name `"test"`, with `EnvCfg.PrometheusPort` pointing at a local `httptest.Server` acting as the fake plugin pprof endpoint).
2. Construct the router via `NewRouter(app, nil)` with no `Authorization` header, no session cookie, and no API token set on the request.
3. Send `POST /plugins/test/debug/pprof/symbol` with body `b=0x1`.
4. Assert response status is `200` (not `401`/`403`) and body matches the content served by the fake plugin backend, proving the request was forwarded and the plugin's response was reflected to an unauthenticated caller.
5. Repeat with `GET /plugins/test/debug/pprof/profile?seconds=1` to confirm the GET path is equally unauthenticated.
6. As a control, assert that `/v2/keys/csa` (authenticated route) returns `401` under identical unauthenticated conditions, demonstrating the inconsistency introduced by `loopRoutes`.

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
