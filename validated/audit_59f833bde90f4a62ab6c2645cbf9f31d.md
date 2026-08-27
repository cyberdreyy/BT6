### Title
Unauthenticated access to `/plugins/:name/metrics` and `/plugins/:name/debug/pprof/*` bypasses all role confinement, including the EI run-only role - ([File: core/web/router.go])

### Summary
`loopRoutes` is registered directly on the base `api` router group with no `auth.Authenticate(...)` middleware at all, unlike every other functional route group (`v2Routes`, `sessionRoutes`, `debugRoutes`). This means `LoopRegistryServer.pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` are reachable by any caller — with or without an external-initiator credential — completely bypassing the run-role confinement that `auth.AuthenticateExternalInitiator` is designed to enforce.

### Finding Description
In `core/web/router.go`, the `api` group is created with only rate-limiting and session-cookie middleware: [1](#0-0) 
`loopRoutes(app, api)` is called on this same unauthenticated `api` group, registering: [2](#0-1) 

Compare this to every other route family, which is wrapped in an `auth.Authenticate(...)` group before being exposed, e.g. `authv2` (token/session) at [3](#0-2)  and `userOrEI` (EI/token/session, forced to `UserRoleRun`) at [4](#0-3) . `loopRoutes` has no such wrapper, so `pluginMetricHandler` in `core/web/loop_registry.go` runs for any request regardless of headers: [5](#0-4)  and the pprof forwarders behave identically [6](#0-5) [7](#0-6) .

`auth.AuthenticateExternalInitiator` explicitly documents and enforces that an EI credential should only ever grant `UserRoleRun`, confined to run-role endpoints: [8](#0-7) . Because `loopRoutes` sits outside any `auth.Authenticate` wrapper, this confinement is never invoked or checked — an EI credential holder (or literally anyone, with no credential) reaches these handlers identically. This exceeds even the reported scope: the routes are not merely accessible to EI credentials in violation of role confinement, they are fully unauthenticated for any caller.

### Impact Explanation
An attacker (EI credential holder, or fully unauthenticated network client if the port is reachable) can:
- Read internal LOOP plugin Prometheus `/metrics` output via `pluginMetricHandler`, potentially disclosing internal operational/telemetry data about plugin/chain state.
- Trigger `/debug/pprof/*` profiling endpoints (`profile`, `trace`, `heap`, `symbol` POST) on internal LOOP processes via `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler`, which can be used for resource-exhaustion DoS (long-running CPU/trace profiles) and information disclosure (heap/goroutine dumps that may contain sensitive in-memory data such as keys or job data held by the plugin process).

This matches a role/authorization-bypass class: an authorization check (`RequiresRunRole`/role confinement) that is present for every other run-scoped endpoint is entirely absent here, allowing privilege escalation beyond the credential's intended scope and unauthorized access to internal node/plugin diagnostics.

### Likelihood Explanation
Very high feasibility and full repeatability: no special preconditions are required beyond network reachability of the node's web server. An external-initiator credential is not even necessary — the routes have zero authentication. Any attacker who can reach the node's HTTP API (including an EI credential holder, who is explicitly scoped in this audit as an in-scope low-privilege actor) can trivially issue `GET /plugins/:name/metrics` or `GET /plugins/:name/debug/pprof/profile` and receive a response identical to what an authenticated admin would get.

### Recommendation
Wrap `loopRoutes` registration in an authenticated group, consistent with the rest of the router. At minimum require `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` and gate with `auth.RequiresAdminRole` (or a dedicated operational role) before invoking `discoveryHandler`, `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler`, since these expose internal profiling/metrics data that should not be reachable by run-role/EI credentials or unauthenticated callers at all.

### Proof of Concept
Go handler-level integration test in `core/web` (extending `loop_registry_test.go` / `router_test.go` patterns):
1. Build the app/router via `NewRouter` with a `LoopRegistry` containing a registered plugin (e.g. `"median"`) with a mocked `EnvCfg.PrometheusPort` backed by an `httptest.Server` returning known metric text.
2. Issue `GET /plugins/median/metrics` with:
   - No auth headers at all → assert `200 OK` and body equals the mocked plugin metrics text (proving anonymous access).
   - `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` for a valid but unrelated EI record → assert identical `200 OK` response, confirming no role check occurred (i.e., `auth.RequiresRunRole` or similar was never invoked).
3. Issue `GET /plugins/median/debug/pprof/heap` with no credentials → assert `200 OK` with pprof profile data returned, proving the diagnostic/profiling surface is also unauthenticated.
4. Contrast with `GET /v2/jobs/:ID/runs` (a genuine EI-scoped `userOrEI` route) using no credentials → assert `401 Unauthorized`, demonstrating that `loopRoutes` uniquely lacks the authentication wrapper applied elsewhere in `router.go`.

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

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

**File:** core/web/loop_registry.go (L96-105)
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

**File:** core/web/auth/auth.go (L145-150)
```go
	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```
