### Title
Unauthenticated exposure of LOOP plugin metrics and pprof endpoints via `loopRoutes` bypassing session/token authentication - (File: core/web/router.go)

### Summary
`loopRoutes` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the top-level `api` group with no auth middleware, unlike the sibling `/v2/debug/pprof` routes which are nested inside `authv2` (requiring `AuthenticateByToken`/`AuthenticateBySession`). Any unauthenticated network client can therefore reach LOOP plugin pprof/metrics proxying handlers.

### Finding Description
In `core/web/router.go`, `NewRouter` builds a base group `api` with only rate-limiting and session middleware (no auth) at [1](#0-0) , and calls `loopRoutes(app, api)` which registers routes directly on that unauthenticated group: [2](#0-1) .

By contrast, the equivalent debug/pprof surface for the node itself is deliberately wrapped: `metricRoutes(authv2)` is only called inside the `authv2` group that requires `auth.AuthenticateByToken`/`auth.AuthenticateBySession` [3](#0-2) [4](#0-3) , and the underlying pprof group definition itself has no auth (`metricRoutes` just builds a subgroup) [5](#0-4) . This confirms the security boundary for pprof-class endpoints in this codebase is enforced by the *caller* wrapping the routes in an authenticated group — and `loopRoutes` fails to do that.

The handlers themselves perform no additional authentication/authorization check — `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` only validate that the named plugin exists in the registry before proxying the request to the internal LOOP process's `/metrics` or `/debug/pprof/*` endpoint and returning the raw response body to the caller [6](#0-5) [7](#0-6) .

Attack flow: an unauthenticated attacker with network access to the node's API port sends `GET /plugins/<name>/debug/pprof/heap` or `.../profile?seconds=30` or `.../metrics`. `pluginPPROFHandler`/`pluginMetricHandler` proxy this straight to the internal LOOP plugin's pprof/metrics endpoint and return the response verbatim — no session cookie, API token, or role check is performed anywhere in the chain.

### Impact Explanation
pprof `heap`/`goroutine`/`allocs` dumps of a LOOP plugin process can disclose in-memory secrets (private keys, credentials, decrypted config) held by the plugin process, and `profile`/`trace` endpoints allow an attacker to trigger CPU profiling for a configurable duration (DoS/resource exhaustion) without any credential. This maps to Chainlink's bounty impact classes for "unauthorized access/disclosure of sensitive information" and "denial of service", falling short of full compromise only because the LOOP registry (`l.registry.Get(pluginName)`) must contain a named plugin for the request to succeed — but this is default/expected node configuration when LOOP plugins are enabled (a configuration state, not an attacker-specific misconfiguration).

### Likelihood Explanation
No credentials, roles, or special network position are required beyond reachability to the chainlink node's HTTP API — the same reachability an unauthenticated client already has for the `/v2/debug/pprof` route were it not gated by `authv2`. This is trivially and repeatably exploitable with a single unauthenticated `curl` request whenever any LOOP plugin is registered.

### Recommendation
Wrap `loopRoutes` registration in an authenticated group consistent with `metricRoutes(authv2)`, e.g. register `/discovery` and `/plugins/*` under `authv2` (or a dedicated group requiring `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)`) instead of the bare `api` group in `NewRouter`.

### Proof of Concept
Go handler-level integration test plan:
1. Build a test `chainlink.Application` with a `LoopRegistry` containing one registered plugin (`name: "test"`, `PrometheusPort` pointing to a local httptest server serving fake `/metrics` and `/debug/pprof/heap`).
2. Call `web.Router(t, app, nil)` to obtain the `*gin.Engine`.
3. Issue `httptest.NewRequest("GET", "/plugins/test/metrics", nil)` and `.../plugins/test/debug/pprof/heap` with no `Authorization` header and no session cookie; assert response status is `200` (proxied) instead of `401`, demonstrating the bypass.
4. Compare against `GET /v2/debug/pprof/heap` with no credentials; assert it returns `401 Unauthorized`, showing the sibling route is protected while `/plugins/*` is not.
5. Table-driven variant: reflect over `engine.Routes()`, and for every route whose path matches `^/plugins/` or `== /discovery`, walk `route.HandlerFunc`/registered middleware chain (or instrument `auth.Authenticate` with a marker) and assert an auth handler is present; the test should currently fail for `loopRoutes`-registered paths, confirming the regression.

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

**File:** core/web/router.go (L185-199)
```go
func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	pprofGroup.POST("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/trace", ginHandlerFromHTTP(pprof.Trace))
	pprofGroup.GET("/allocs", ginHandlerFromHTTP(pprof.Handler("allocs").ServeHTTP))
	pprofGroup.GET("/block", ginHandlerFromHTTP(pprof.Handler("block").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/mutex", ginHandlerFromHTTP(pprof.Handler("mutex").ServeHTTP))
	pprofGroup.GET("/threadcreate", ginHandlerFromHTTP(pprof.Handler("threadcreate").ServeHTTP))
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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** core/web/loop_registry.go (L150-215)
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
