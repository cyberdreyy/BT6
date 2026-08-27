### Title
Unauthenticated access to `/plugins/:name/metrics` exposes LOOP plugin Prometheus metrics - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
`loopRoutes` registers `GET /plugins/:name/metrics` (and the sibling `/discovery` and pprof endpoints) directly on the top-level `api` router group with only rate limiting and session middleware attached — no `auth.Authenticate` wrapper is applied, unlike other sensitive routes such as `/debug/vars` or `DELETE /sessions`. Any unauthenticated network client can therefore call `pluginMetricHandler` and receive the raw Prometheus text scraped from the corresponding LOOP plugin's internal metrics endpoint.

### Finding Description
In `core/web/router.go`, `NewRouter` builds an `api` group with only rate limiting and cookie-session middleware attached (`core/web/router.go:78-91`), then calls `loopRoutes(app, api)` with no additional authentication wrapper: [1](#0-0) 
Compare this to `debugRoutes`, which explicitly wraps `/debug/vars` behind `auth.Authenticate(...)`: [2](#0-1) 
and `sessionRoutes`, which creates a separate authenticated sub-group for `DELETE /sessions`: [3](#0-2) 

`loopRoutes` registers the plugin routes with no such wrapper: [4](#0-3) 

`pluginMetricHandler` itself performs no session/auth check — it only looks up the plugin name in the registry and proxies the request to the plugin's internal Prometheus port, returning the raw response body verbatim: [5](#0-4) 

The existing integration test `TestLoopRegistry` calls `client.Get(expectedLooppEndPoint)` using `app.NewHTTPClient(nil)`, which is a session-authenticated admin client by default (`core/internal/cltest/cltest.go:721-748`), so the existing test suite does not exercise or assert an unauthenticated-request rejection path — it never proves that authentication is required, and inspecting the router construction confirms none is enforced.

### Impact Explanation
This is an information-disclosure issue: the metrics text returned by LOOP plugins (and the `/discovery` endpoint listing all plugin metrics targets) may include labels/values with operational details — plugin names, ports, and potentially job- or account-related identifiers depending on what each plugin instruments. Any unauthenticated network peer able to reach the node's API port can retrieve this data, matching a Chainlink bounty "information disclosure of internal operational metrics" impact class. It does not directly grant fund movement or key disclosure by itself, but it discloses internal node state to an unauthenticated caller in violation of `AUTHENTICATION_SOUNDNESS`.

### Likelihood Explanation
Precondition is only network reachability to the node's HTTP API port; no credentials, tokens, or session cookie are required. The plugin name must be known/guessed, but plugin names are also exposed unauthenticated via `/discovery`, making full enumeration trivial. This is fully reproducible against any deployment where LOOP plugins are registered and the API port is network-accessible.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/metrics` and `/discovery` routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` middleware used for `/debug/vars` and other sensitive endpoints, or gate access to an operator/internal-only network path.

### Proof of Concept
Go handler-integration test plan (in `core/web`):
1. Start a test `chainlink.Application` via `cltest.NewApplicationEVMDisabled(t)` and `app.Start(ctx)`.
2. Register a LOOP plugin via `app.GetLoopRegistry().Register("testplugin")` and run a mock Prometheus-serving loop on the assigned port (as in `TestLoopRegistry`).
3. Issue a raw, unauthenticated `http.Get` (using `http.DefaultClient`, NOT `app.NewHTTPClient`, and without any session cookie) to `app.Server.URL + "/plugins/testplugin/metrics"`.
4. Assert the response status is `200 OK` and the body contains the plugin's metric text — demonstrating the request succeeded with zero authentication.
5. Contrast with a similarly-constructed unauthenticated request to `/debug/vars`, which should be rejected/redirected due to `auth.Authenticate` middleware, to confirm the inconsistency in route protection.

### Citations

**File:** core/web/router.go (L86-91)
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

**File:** core/web/router.go (L207-218)
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
