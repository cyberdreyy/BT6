Confirmed: `loopRoutes(app, api)` at [1](#0-0)  registers `/plugins/:name/debug/pprof/symbol` (and the sibling `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile` routes) directly on the base `api` group with no authentication middleware, unlike every other sensitive route in the file which is wrapped in `auth.Authenticate(...)` (see `debugRoutes`, `sessionRoutes`, `v2Routes`/`authv2`, and even the node's own `/v2/debug/pprof/*` via `metricRoutes(authv2)` at [2](#0-1)  and [3](#0-2) ).

### Title
Unauthenticated POST forwarding to internal plugin pprof `/symbol` endpoint - ([File: core/web/loop_registry.go])

### Summary
`loopRoutes` registers `/plugins/:name/debug/pprof/symbol` (and sibling pprof/metrics/discovery routes) on the bare `api` router group without any `auth.Authenticate(...)` middleware, unlike every other web-server route including the node's own `/v2/debug/pprof/*` routes which require session/token auth. As a result, any unauthenticated network client can `POST` an arbitrary body to this endpoint and have it forwarded verbatim by `LoopRegistryServer.pluginPPROFPOSTSymbolHandler` to the internal plugin's `/debug/pprof/symbol` HTTP endpoint via `doRequest`, with the plugin's response relayed back to the caller.

### Finding Description
`loopRoutes(app, api)` is called from `NewRouter` on the plain `api` group with no authentication wrapper: [4](#0-3)  and [1](#0-0) . This contrasts with every other route group in the file (`debugRoutes`, `sessionRoutes`'s authenticated group, `authv2` used for `metricRoutes` which exposes the node's own pprof at `/v2/debug/pprof/*`), which are explicitly gated by `auth.Authenticate(app.AuthenticationProvider(), ...)`.

`pluginPPROFPOSTSymbolHandler` reads `gc.Param("name")`, looks up the plugin in the registry, reads the full request body unauthenticated, and forwards it as a POST to `http://<loopHostName>:<PrometheusPort>/debug/pprof/symbol` via `doRequest`, then relays the plugin's raw response back to the caller: [5](#0-4) . No session cookie, API token, or external-initiator signature is checked anywhere in this call path, so any network client that can reach the node's HTTP port can invoke it.

### Impact Explanation
The impact is unauthenticated information disclosure: an attacker can query the pprof `symbol` endpoint of any registered LOOP plugin (address-to-function-name resolution, and via the related `pluginPPROFHandler` GET routes, full heap/goroutine/profile/trace dumps) without any credentials. This can leak internal memory addresses, goroutine stacks, and other runtime details useful for further exploitation or reveal implementation details/internal function names of the plugin process, matching a "sensitive data exposure" class impact rather than fund loss or key compromise.

### Likelihood Explanation
Feasibility is high and requires no privileges: any client with network access to the Chainlink node's web server port and a registered plugin name can trigger this without a login, API token, or external-initiator key. The request is straightforward and repeatable (simple `POST` to a known path pattern).

### Recommendation
Wrap `loopRoutes(app, api)` registrations (or at minimum the `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for the node's own pprof/debug routes, and consider requiring an admin/edit role via `auth.RequiresAdminRole` given the sensitivity of profiling/debug data.

### Proof of Concept
1. In a `core/web` handler-level test (e.g., extend `loop_registry_internal_test.go` or add a router test), construct a `gin.Engine` via `NewRouter` (or directly register `loopRoutes`) with a test `chainlink.Application` and a registered test plugin (`plugins.NewTestLoopRegistry`).
2. Send `httptest.NewRequest("POST", "/plugins/<name>/debug/pprof/symbol", body)` with no `Cookie` header and no `Authorization` header.
3. Assert the request is NOT rejected with `401`/`403` and instead reaches `pluginPPROFPOSTSymbolHandler`, which calls `doRequest` against a mock/local HTTP server standing in for the plugin's pprof endpoint, and returns `http.StatusOK` with the mock's body echoed back — proving the endpoint is reachable and forwards attacker-controlled data without authentication.

### Citations

**File:** core/web/router.go (L87-91)
```go
	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
```

**File:** core/web/router.go (L180-199)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}

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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
