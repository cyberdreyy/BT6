This confirms the vulnerability.

### Title
Unauthenticated LOOP plugin pprof endpoint exposes in-memory secrets via `/plugins/:name/debug/pprof/*profile` - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `GET /plugins/:name/debug/pprof/*profile` (and the `symbol` POST variant) directly on the base `api` router group without any authentication middleware, unlike every other sensitive endpoint in the router (`debugRoutes`, `sessionRoutes`, `v2Routes`, `metricRoutes`). This allows any unauthenticated network client to pull live pprof heap/goroutine/allocs dumps proxied from a running LOOP plugin process (e.g. the median relayer plugin), which can contain memory-resident secrets such as private keys or tokens.

### Finding Description
`core/web/router.go` builds an `api` group with only rate-limiting and session middleware (no auth): [1](#0-0) 

`loopRoutes(app, api)` registers the pprof proxy routes on this unauthenticated group: [2](#0-1) 

Compare this to `debugRoutes`, which wraps the analogous host-level `/debug/vars` endpoint with `auth.Authenticate(..., auth.AuthenticateBySession)`: [3](#0-2) 

and `metricRoutes` (the host's own `/debug/pprof/*` handlers), which is only mounted inside the authenticated `authv2` group in `v2Routes`: [4](#0-3) [5](#0-4) 

The actual handler, `pluginPPROFHandler` in `core/web/loop_registry.go`, takes the `:name` and `*profile` path params, builds a URL to the plugin's internal prometheus port, and proxies the raw response body back to the caller with no authorization check of its own: [6](#0-5) 

Because no auth middleware is attached to the `loopRoutes` group and the handler itself performs no authentication/authorization check, any unauthenticated client can send `GET /plugins/median/debug/pprof/heap` and receive the plugin's live heap dump, which can contain private keys, secrets, or other sensitive in-memory data held by the plugin process.

### Impact Explanation
This matches the "memory-resident secret disclosure via pre-auth debug endpoint" bounty impact class: an unauthenticated remote attacker can dump heap/goroutine profiles of a LOOP plugin process (e.g., a chain-specific relayer plugin handling private keys), potentially leaking private key material, API secrets, or other sensitive runtime state without any credentials.

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's HTTP API and at least one LOOP plugin being registered/running (which is standard in LOOPP deployments, e.g. for `median`). The request is a single unauthenticated `GET`, fully repeatable, and requires no special role, token, or session — the lowest possible attacker capability bar.

### Recommendation
Wrap `loopRoutes`'s pprof (and ideally the `/plugins/:name/metrics` endpoint too, though metrics are lower sensitivity) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used elsewhere (e.g. as in `v2Routes`'s `authv2` group), and apply an appropriate role check (at minimum admin/view role) before forwarding requests to the plugin's internal pprof port. This should mirror how `metricRoutes` is nested inside `authv2` rather than being mounted on the unauthenticated base group.

### Proof of Concept
Go handler-level integration test plan (e.g. in `core/web/loop_registry_test.go` or `core/web/router_test.go`):
1. Start a test `chainlink.Application` with the router built via `web.Router` / `web.NewRouter`, and register a fake/stub LOOP plugin in the loop registry exposing a fake prometheus port that serves `net/http/pprof` handlers.
2. Using an HTTP client with **no** `Authorization`/session cookie/API-key headers set (unlike `app.NewHTTPClient` helpers used elsewhere which attach auth), issue `GET /plugins/<pluginName>/debug/pprof/heap`.
3. Assert the response status is `200 OK` (not `401 Unauthorized`) and that the body contains valid pprof binary/profile data (e.g. parseable via `github.com/google/pprof/profile`).
4. As a comparison assertion, issue `GET /v2/keys/eth` (an `authv2` route) with the same unauthenticated client and confirm it returns `401`, demonstrating the inconsistency between `loopRoutes` and `v2Routes` auth enforcement.

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

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
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

**File:** core/web/router.go (L445-446)
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
