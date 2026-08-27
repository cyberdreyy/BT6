### Title
Unauthenticated proxy exposes internal plugin pprof endpoints (GET and POST /symbol) - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` routes are registered on the base `api` router group in `core/web/router.go` without any authentication middleware, unlike every other debug/pprof-capable route in the node (`debugRoutes`, `metricRoutes` under `authv2`). This allows an unauthenticated attacker to reach `LoopRegistryServer.pluginPPROFPOSTSymbolHandler`, which reads the raw POST body and forwards it unauthenticated to the internal LOOP plugin's pprof HTTP server.

### Finding Description
In `core/web/router.go`, `loopRoutes(app, api)` is called at [1](#0-0)  directly on the `api` group, whose only middlewares are the rate limiter and cookie session store [2](#0-1)  — no `auth.Authenticate(...)` wrapper is applied. This is in stark contrast to the node's own `/v2/debug/pprof/*` routes, which are deliberately placed inside the `authv2` group requiring `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` before `metricRoutes(authv2)` is invoked [3](#0-2) [4](#0-3) , and the plain `/debug/vars` route which requires session auth [5](#0-4) .

Concretely, `loopRoutes` registers:
```
r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
``` [6](#0-5) 

`pluginPPROFPOSTSymbolHandler` looks up the plugin by the `:name` path param, reads the full request body unauthenticated, and forwards it via `doRequest` to `http://<loopHostName>:<PrometheusPort>/debug/pprof/symbol` on the internal plugin process, returning the response body directly to the caller: [7](#0-6) . There is no call to `auth.Authenticate`, no session check, and no token validation anywhere in this handler or its call chain — any unauthenticated HTTP client that can reach the node's web server can invoke it.

### Impact Explanation
This breaks the invariant that "requests are bound to one authenticated sender" for a class of debug endpoints that expose internal-process information disclosure (function/symbol resolution for arbitrary addresses via pprof `/debug/pprof/symbol`, and similarly `pluginPPROFHandler` exposes `/debug/pprof/profile`, `/debug/pprof/trace`, `/debug/pprof/heap`, etc. at [8](#0-7) ). An unauthenticated attacker can trigger CPU/heap profiling and full-symbol resolution of the LOOP plugin process, which can leak internal memory addresses, binary layout, and function names useful for further exploitation, and can be abused to consume resources (profile/trace with attacker-controlled `seconds` query param) as an unauthenticated denial-of-service vector against the plugin process. This is an authentication-bypass exposing sensitive debug/introspection surface, matching the "authentication bypass on internal debug endpoint" bounty class rather than direct fund loss.

### Likelihood Explanation
No credentials are required — a plain unauthenticated HTTP POST/GET to the node's public web server on the known route pattern `/plugins/:name/debug/pprof/...` reaches the handler, provided a plugin is registered in `l.registry` (LOOP registry) with a known name. This is trivially reproducible with any HTTP client and is fully repeatable.

### Recommendation
Wrap `loopRoutes` (or at minimum the pprof-forwarding routes) with the same authentication middleware used for `/v2/debug/pprof` and `/debug/vars`, e.g. register them under a group using `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (and consider `auth.RequiresAdminRole` given the sensitivity of profiling/tracing internal plugin processes), consistent with `debugRoutes` and `metricRoutes`.

### Proof of Concept
1. Build a `gin.Engine` using `NewRouter` (or directly wire `loopRoutes` on a fresh `gin.Engine`) with a `chainlink.Application` mock whose `GetLoopRegistry()` returns a registry containing one registered plugin (`Name: "test-plugin"`, `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` acting as the fake plugin's pprof backend that returns HTTP 200 with a canned response body).
2. Using `httptest.NewRequest(http.MethodPost, "/plugins/test-plugin/debug/pprof/symbol", bytes.NewBufferString("0x1234"))` with **no** `Authorization` header, **no** session cookie, send it through the router via `httptest.NewRecorder()` and `engine.ServeHTTP`.
3. Assert the fake plugin backend received the POST body and that the response recorder returns `http.StatusOK` with the plugin's canned body echoed back to the (unauthenticated) caller — demonstrating a fully unauthenticated write-capable proxy to an internal debug endpoint.
4. Repeat with `GET /plugins/test-plugin/debug/pprof/heap` (via `pluginPPROFHandler`) to show the broader unauthenticated pprof forwarding surface.
5. Contrast with a call to `/v2/debug/pprof/symbol` without credentials, which should be rejected with `401 Unauthorized` by `auth.Authenticate`, confirming the routing/middleware inconsistency in `core/web/router.go`.

### Citations

**File:** core/web/router.go (L78-85)
```go
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)
```

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

**File:** core/web/loop_registry.go (L168-215)
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
