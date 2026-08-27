### Title
Missing authentication on LOOP plugin pprof endpoints allows any caller (including restricted view-role tokens) to trigger internal profiling/debug data collection - (File: core/web/router.go, core/web/loop_registry.go)

### Summary
The `loopRoutes` function registers `/plugins/:name/debug/pprof/*profile` and `/plugins/:name/debug/pprof/symbol` directly on the base router group with no `auth.Authenticate` middleware wrapper, unlike every other sensitive route group (`debugRoutes`, `sessionRoutes`, `v2Routes`). As a result, `LoopRegistryServer.pluginPPROFHandler` and `pluginPPROFPOSTSymbolHandler` are reachable by anyone who can send an HTTP request to the node, and any `Authorization` header (including a view-role API token) is silently ignored since no auth check ever inspects it.

### Finding Description
In `core/web/router.go`, compare route group construction:
- `debugRoutes` wraps `/debug/vars` with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [1](#0-0) 
- `v2Routes` wraps its `authv2` group with `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)` and further applies `auth.RequiresEditRole`/`auth.RequiresAdminRole`/`auth.RequiresRunRole` per-route [2](#0-1) 
- `loopRoutes`, however, registers its handlers directly on `r` (the base `api` group) with no auth middleware at all: [3](#0-2) 

Because there is no `auth.Authenticate*` call in the chain for these routes, the gin context never runs any credential/role check before invoking `LoopRegistryServer.pluginPPROFHandler`, which simply looks up the plugin by name and proxies the request to the plugin's internal pprof HTTP endpoint [4](#0-3) , forwarding the response body verbatim with `gc.Data(http.StatusOK, ...)` [5](#0-4) . The `/discovery` and `/plugins/:name/metrics` routes are documented/intended to be exposed this way for Prometheus scraping (per `plugins/README.md`), but the pprof profiling and symbol endpoints are debug primitives that expose runtime internals (heap dumps, goroutine stacks, CPU profiles, symbol tables) of the plugin process, and were clearly intended to require the same authentication as `/debug/vars` — the omission of any `auth.Authenticate` wrapper appears to be a gap rather than an intentional public exposure.

### Impact Explanation
Any caller — unauthenticated, or holding only a view/run-role API token whose `Authorization` header is never even evaluated on this path — can pull CPU/heap/goroutine profiles and pprof symbol data from a running LOOP plugin process. Memory/heap profiles and goroutine dumps can leak sensitive in-process state (buffered secrets, key material handled by relayer/median plugins, internal addresses/config) and provide a reconnaissance/DoS primitive (CPU profile collection with attacker-controlled `seconds` parameter can tie up plugin resources). This directly matches the audited invariant violation: a low-privilege/no-privilege actor reaches functionality equivalent to admin/debug access, independent of the token's actual role — i.e., role/authorization bypass on an internal debug capability.

### Likelihood Explanation
Trivial and fully reproducible: no valid credentials are required at all (an unauthenticated GET is sufficient), so a view-role API token is more than enough. Any client of the node's HTTP API that knows/guesses a running plugin name (discoverable via the also-unauthenticated `/discovery` endpoint) can hit the endpoint repeatedly with no rate/role restriction beyond the generic `rateLimiter` applied to the whole `api` group.

### Recommendation
Wrap `loopRoutes`' pprof/symbol registrations with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` used elsewhere, and require at minimum an admin/run role (consistent with `/debug/vars`'s protection level) before invoking `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler`. The `/discovery` and `/plugins/:name/metrics` routes can remain distinct if they are intentionally meant for unauthenticated Prometheus scraping, but the debug/pprof subpaths should not share that exemption.

### Proof of Concept
Add a table-driven test in `core/web/loop_registry_test.go` (or a new handler test) that:
1. Builds an app via `cltest.NewApplicationWithConfigAndKey`/`cltest.NewApplicationEVMDisabled`, registers a mock LOOP plugin as in the existing `TestLoopRegistry` test.
2. Creates an HTTP client with **no** credentials (`app.NewHTTPClient(nil)`) and separately one built from a view-role user's API token (`cltest.CreateUserWithSession`/token helper with `UserRoleView`).
3. Sends `GET /plugins/mockLoopImpl/debug/pprof/profile?seconds=1` (and `/debug/pprof/symbol` POST) with each client.
4. Asserts the response status is `200 OK` (proxied) in both cases, rather than `401 Unauthorized`/`403 Forbidden`, proving the endpoint bypasses authentication and role checks entirely — contrasted against a `GET /debug/vars` request with the same unauthenticated client which should return `401`.

### Citations

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

**File:** core/web/router.go (L245-257)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
		uc := UserController{app}
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)
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
