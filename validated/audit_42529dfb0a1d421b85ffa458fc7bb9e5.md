### Title
Unauthenticated access to LOOP plugin pprof/metrics/discovery endpoints - ([File: core/web/router.go])

### Summary
The `loopRoutes` handler group registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the top-level `api` router group with no authentication middleware, unlike every other sensitive route group in the file. This allows a completely unauthenticated attacker to reach the underlying handler and pull heap/goroutine/profile dumps and Prometheus metrics from LOOP plugin processes.

### Finding Description
`NewRouter` wires up `debugRoutes(app, api)` and `loopRoutes(app, api)` at the same level [1](#0-0) . `debugRoutes` intentionally wraps its single `/debug/vars` route behind `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [2](#0-1) . `loopRoutes`, by contrast, registers its routes directly on `r` with zero auth middleware: [3](#0-2) 

These routes are backed by `LoopRegistryServer.discoveryHandler`, `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler`, none of which perform any authentication or role check internally — they only validate the plugin name exists before proxying the request to the internal LOOP plugin's `/metrics` or `/debug/pprof/*` endpoints and returning the raw response body to the caller [4](#0-3) [5](#0-4) .

Because there is no `auth.Authenticate(...)` wrapper (as used for every `authv2` and `/debug` route) applied to these handlers, any unauthenticated client of the node's HTTP API can hit these endpoints directly and get the handler to execute — never encountering a 401/403 response at all.

### Impact Explanation
An unauthenticated remote attacker can retrieve `pprof` profiles (`heap`, `goroutine`, `profile`, `trace`, `symbol`) and Prometheus metrics from any registered LOOP plugin process. This can leak sensitive runtime data (goroutine stacks, memory contents that may include key material or secrets processed in plugin memory, internal addressing/service-discovery information), and CPU/heap profiling endpoints can be abused for resource-exhaustion/DoS by requesting expensive profiles (e.g., long `seconds=` CPU profile capture) repeatedly. This matches the bounty class of authentication bypass / unauthorized information disclosure.

### Likelihood Explanation
No credentials, session, API token, or role are required — the attacker only needs network access to the node's exposed HTTP API port, which the threat model treats as reachable by an "unauthenticated client of the node API." The endpoint is deterministic and repeatable on every request.

### Recommendation
Wrap `loopRoutes` registration with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (or an appropriate role-gated) middleware used for `debugRoutes`, so unauthenticated requests receive 401/403 and never reach the plugin-proxying handlers.

### Proof of Concept
Go handler-level integration test plan:
1. Build the router via `web.Router(t, app, nil)` without any authenticated client/session.
2. Register a fake plugin in the `LoopRegistry` so `Get(pluginName)` succeeds.
3. Issue `GET /discovery` with no auth headers/cookies — assert current behavior returns `200 OK` (handler executed) instead of `401/403`.
4. Issue `GET /plugins/<name>/debug/pprof/heap` and `POST /plugins/<name>/debug/pprof/symbol` with no auth — assert handler forwards the request and returns plugin data instead of an auth error.
5. After applying the fix (wrapping the group with `auth.Authenticate`), re-run the same requests and assert `401 Unauthorized`/`403 Forbidden` is returned and the proxy handler (`pluginPPROFHandler`/`discoveryHandler`) is never invoked (e.g., via a call-counter flag in a stubbed `LoopRegistryServer`).

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
