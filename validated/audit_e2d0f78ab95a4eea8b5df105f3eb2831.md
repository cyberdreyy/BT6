### Title
Unauthenticated LOOP plugin metrics/pprof endpoints leak plugin existence and full internal data with no role check - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*`, and `/plugins/:name/debug/pprof/symbol` routes are registered on the base API group with no `auth.Authenticate` wrapper, so any unauthenticated caller can hit `LoopRegistryServer.pluginMetricHandler` / `pluginPPROFHandler` and use the distinct 404 ("plugin %q does not exist") vs 200 responses as an oracle to enumerate valid internal LOOP plugin names. Because the check gates only existence and not access, a successful lookup goes further than mere enumeration — it proxies live metrics/pprof data from the internal plugin process back to the unauthenticated caller.

### Finding Description
`loopRoutes` registers these handlers directly on the `api` group in `core/web/router.go`: [1](#0-0) 

Unlike `sessionRoutes`, which explicitly wraps privileged routes with `auth.Authenticate(...)`: [2](#0-1) 

`loopRoutes` has no such wrapper, and the enclosing `api` group only applies rate limiting and session middleware, not authentication: [3](#0-2) 

Inside the handlers, `pluginName := gc.Param("name")` (attacker-controlled URL param) is looked up via `l.registry.Get(pluginName)`. On miss, the handler returns HTTP 404 with `html.EscapeString(pluginName)` reflected in the body; on hit, it proceeds to proxy a request to the internal plugin's Prometheus/pprof endpoint and returns the response body with HTTP 200: [4](#0-3) [5](#0-4) 

The same pattern repeats in `pluginPPROFPOSTSymbolHandler`: [6](#0-5) 

There is no role/session check anywhere in these functions, so the 404-vs-200 status code difference (independent of the HTML-escaping of the reflected name) is a reliable, unauthenticated oracle for whether a given plugin name is registered in the node's `LoopRegistry`. Registered plugin names correspond to internal LOOP infrastructure components (e.g., median, mercury, OCR2 plugins) that should only be visible/queryable to an authenticated admin/edit-role user, similar to how other admin-only diagnostics (`debugRoutes`, `pprof`) are typically protected.

### Impact Explanation
An unauthenticated attacker can: (1) enumerate which LOOP plugins are actively registered on the node, revealing internal infrastructure/configuration that should require an authenticated, appropriately-roled session; and (2) for any correctly-guessed/known plugin name, retrieve that plugin's live Prometheus metrics and full pprof profiles (heap, goroutine, cpu profile, `/debug/pprof/symbol`) — information disclosure of internal process state without any credential. This maps to Chainlink's "Information Disclosure" / unauthorized access to internal node data impact class; it does not directly move funds but materially weakens the confidentiality guarantee that only admin/edit-role users can view infrastructure inventory and diagnostics.

### Likelihood Explanation
Trivial to exploit: no credentials, tokens, or special network position are required — a plain unauthenticated HTTP GET to `/plugins/<guess>/metrics` or `/plugins/<guess>/debug/pprof/heap` from the public node API surface is sufficient. Plugin names are a small, guessable/known set (e.g., `median`, `mercury`, common OCR2 plugin identifiers), making enumeration highly feasible and fully repeatable.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/...` sub-routes) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` and an appropriate role check (e.g., admin/edit role), consistent with how `sessionRoutes` protects the `DELETE /sessions` route. Additionally, consider not reflecting the raw plugin name in error responses and returning a generic 404 to avoid any residual oracle behavior even once authentication is enforced.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/loop_registry_test.go`):
1. Start the app/router as in `TestLoopRegistry` but use an HTTP client that does NOT authenticate (no session cookie / API token), i.e., a raw `http.Client` against `app.Server.URL` rather than `app.NewHTTPClient(nil)` if that helper auto-authenticates.
2. Register one plugin (`"mockLoopImpl"`) via `app.GetLoopRegistry().Register(...)`.
3. Issue unauthenticated `GET /plugins/mockLoopImpl/metrics` and assert `http.StatusOK` is returned with plugin metrics body, proving no auth/role check blocks it.
4. Issue unauthenticated `GET /plugins/doesNotExist/metrics` and assert `http.StatusNotFound` with body containing `"plugin \"doesNotExist\" does not exist"`.
5. Assert the two responses differ in status code/body without any `Authorization`/session cookie present, and repeat for `/plugins/:name/debug/pprof/heap` and `/plugins/:name/debug/pprof/symbol` (POST) to confirm the same unauthenticated oracle and data exposure across all three routes registered in `loopRoutes` (`core/web/router.go` lines 230-235).

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

**File:** core/web/router.go (L207-217)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
```

**File:** core/web/router.go (L230-235)
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
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
