### Title
Unauthenticated LOOP plugin pprof endpoint discloses process memory/profiling data - ([File: core/web/loop_registry.go])

### Summary
The route `GET /plugins/:name/debug/pprof/*profile` is registered in `loopRoutes` without any authentication middleware, unlike other sensitive routes in the same router (`sessionRoutes`, `v2Routes`) which explicitly wrap their groups with `auth.Authenticate(...)`. This lets any unauthenticated client enumerate a LOOP plugin name via `/discovery` and then pull that plugin's pprof debug data through `LoopRegistryServer.pluginPPROFHandler`.

### Finding Description
`loopRoutes` registers all LOOP-related endpoints directly on the passed-in `*gin.RouterGroup` with no auth wrapper: [1](#0-0) 

Compare this to `sessionRoutes`, which explicitly creates an `unauth` group for the login endpoint and a separate `auth` group protected by `auth.Authenticate(...)` for authenticated actions: [2](#0-1) 

and `v2Routes`, which similarly separates `unauthedv2` from `authv2` (wrapped in `auth.Authenticate`) for sensitive user/admin endpoints: [3](#0-2) 

`loopRoutes` has no such split — `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and the POST symbol endpoint are all unauthenticated by construction.

The handler chain: `discoveryHandler` lists all registered plugin names via `l.registry.List()` and exposes them in the JSON discovery payload: [4](#0-3) 

An attacker uses a discovered/guessed plugin name to call `pluginPPROFHandler`, which looks up the plugin, builds a URL to the plugin's internal `/debug/pprof/<profile>` endpoint (e.g. `heap`, `goroutine`, `allocs`), and forwards the request via `doRequest`: [5](#0-4) 

`doRequest` performs the outbound HTTP call and streams the response body directly back to the unauthenticated caller with a 200 status and no redaction: [6](#0-5) 

pprof heap/goroutine dumps can contain sensitive in-memory data (key material, config values, internal state) held by the LOOP plugin process. Because no `auth.Authenticate` (or role/API-token) check exists anywhere in `loopRoutes`, none of the existing session/token/role middleware in this codebase stops the request.

### Impact Explanation
This is unauthenticated disclosure of internal plugin process memory/profiling data (pprof heap, goroutine, allocs, block, mutex, threadcreate, trace, symbol), reachable by any network client without credentials. Depending on what the LOOP plugin holds in memory (e.g., decrypted keys, config secrets), this can lead to secret/key material disclosure — matching the "sensitive data exposure / key or secret disclosure" bounty impact class.

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's web server and knowledge of a plugin name, which is itself obtainable unauthenticated via `GET /discovery`. The full chain (`discovery` → `pluginPPROFHandler` → `doRequest`) requires no session, token, or role, making this trivially and repeatably exploitable by any unauthenticated attacker with network access to the API/gateway.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used elsewhere, and consider requiring admin role via `auth.RequiresAdminRole` given the sensitivity of pprof/memory-dump data. If external Prometheus scraping requires `/discovery` and `/metrics` to remain unauthenticated, keep those separate from the pprof/debug routes which should never be exposed without authentication.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a `chainlink.Application` test harness with `LoopRegistry` containing one registered plugin (`name="median"`, with `EnvCfg.PrometheusPort` pointing to a local test HTTP server that serves a fake `net/http/pprof`-style handler at `/debug/pprof/heap` returning a fixed byte payload).
2. Build the full gin router via the code path that calls `loopRoutes(app, r)` (same as production `router.go`), without injecting any auth header/session cookie/API token.
3. Issue `GET /discovery` and assert the plugin name `median` appears in the response body (confirms the discovery step of the attack chain).
4. Issue `GET /plugins/median/debug/pprof/heap` with no `Authorization` header and no session cookie.
5. Assert the response status is `200 OK` and the body equals the fake pprof payload — proving the endpoint returns real backend data instead of `401 Unauthorized`/`403 Forbidden`.
6. As a regression guard, add a second assertion that after the fix, the same unauthenticated request returns `401`/`403`, and that a request with a valid session/token succeeds.

### Citations

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

**File:** core/web/router.go (L238-248)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/loop_registry.go (L53-65)
```go
func (l *LoopRegistryServer) discoveryHandler(w http.ResponseWriter, req *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	groups := make([]*targetgroup.Group, 0, 1+len(l.registry.List()))

	// add node metrics to service discovery
	groups = append(groups, pluginGroup(l.discoveryHostName, l.exposedPromPort, "/metrics"))

	// add all the plugins
	for _, registeredPlugin := range l.registry.List() {
		group := pluginGroup(l.discoveryHostName, l.exposedPromPort, pluginMetricPath(registeredPlugin.Name))
		group.Labels[LabelMetaPluginName] = model.LabelValue(registeredPlugin.Name)
		groups = append(groups, group)
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
