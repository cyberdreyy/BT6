### Title
Unauthenticated information disclosure of internal LOOP plugin topology via GET /discovery - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
`loopRoutes` registers `GET /discovery` (and the `/plugins/:name/*` endpoints) on the shared `RouterGroup` without any `auth.Authenticate` middleware, unlike every other sensitive route group in `NewRouter` (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any network client that can reach the node's HTTP API can call this endpoint with no credentials and receive the full list of registered LOOP plugin names and their scrape target hostnames/ports.

### Finding Description
`NewRouter` wires up route groups in `core/web/router.go`: `debugRoutes` wraps its group with `auth.Authenticate(...)` [1](#0-0) , `sessionRoutes` and `v2Routes` likewise gate mutating/sensitive endpoints behind `auth.Authenticate`/`auth.RequiresEditRole` etc. [2](#0-1) [3](#0-2) . However `loopRoutes` registers its routes directly on the plain `r` group with no authentication middleware at all: [4](#0-3) . This group is mounted at `NewRouter` line 91 on the top-level `api` group, which only carries rate-limiting and session-cookie middleware, not authentication [5](#0-4) .

`discoveryHandler` itself performs no authorization check — it iterates `l.registry.List()` and returns a Prometheus service-discovery JSON body containing the discovery hostname, the exposed Prometheus port, and the name/metrics path of every registered LOOP plugin: [6](#0-5) . The existing test `TestLoopRegistry` in `core/web/loop_registry_test.go` calls `client.Get("/discovery")` using the generic `app.NewHTTPClient(nil)` (no session/token) and asserts a `200 OK` with the plugin list in the body, confirming the route is reachable without authentication: [7](#0-6) .

### Impact Explanation
An unauthenticated attacker who can reach the node's web server can enumerate the node's internal LOOP plugin topology (plugin names, internal hostnames, and Prometheus ports used for metrics scraping) via a single unauthenticated GET request. This is an internal-topology/information-disclosure leak (reconnaissance aid for further attacks against internal plugin metrics/pprof endpoints), matching a low/informational disclosure impact class rather than direct fund loss or credential compromise — no secrets, private keys, or job data are exposed by this specific endpoint.

### Likelihood Explanation
Trivial and fully repeatable: no credentials, cookies, or tokens are required; a single raw `GET /discovery` HTTP request against any reachable Chainlink node with `prometheus`-based LOOP plugins configured returns `200` with the full body, as demonstrated by the existing (unauthenticated) test client interaction.

### Recommendation
Wrap `loopRoutes` in an authenticated route group (consistent with `debugRoutes`), e.g. mount it via `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))` or restrict it to loopback/internal-only network access, so `/discovery` and `/plugins/:name/*` require the same session/token authentication as other sensitive introspection endpoints.

### Proof of Concept
Go handler-level test plan:
1. Build a `gin.Engine` (or reuse `web.NewRouter`) with a `chainlink.Application` mock that has at least one registered LOOP plugin (as in `core/web/loop_registry_test.go`'s `mockLoopImpl` setup).
2. Issue `GET /discovery` using a raw `http.Client` (no cookie jar, no `Authorization` header) — do not use `app.NewHTTPClient` with a session, but a bare client to explicitly confirm the absence of auth requirements.
3. Assert response status is `200 OK` (not `401 Unauthorized`).
4. Unmarshal the JSON body into `[]*targetgroup.Group` and assert it contains the expected plugin name label (`web.LabelMetaPluginName`) and target host:port, proving plugin topology is disclosed without any credential.
5. As a contrast/negative assertion, repeat the same unauthenticated request against `/v2/jobs` and confirm it returns `401`, demonstrating that `/discovery` is inconsistent with the rest of the authenticated API surface.

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

**File:** core/web/router.go (L245-256)
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
```

**File:** core/web/loop_registry.go (L52-81)
```go
// discoveryHandler implements service discovery of prom endpoints for LOOPs in the registry
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

	b, err := l.jsonMarshalFn(groups)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, err = w.Write([]byte(err.Error()))
		if err != nil {
			l.logger.Error(err)
		}
		return
	}
	_, err = w.Write(b)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		l.logger.Error(err)
	}
}
```

**File:** core/web/loop_registry_test.go (L99-122)
```go
	client := app.NewHTTPClient(nil)

	t.Run("discovery endpoint", func(t *testing.T) {
		t.Parallel()
		// under the covers this is routing thru the app into loop registry
		resp, cleanup := client.Get("/discovery")
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("discovery response %s", b)
		var got []*targetgroup.Group
		require.NoError(t, json.Unmarshal(b, &got))

		gotLabels := make([]model.LabelSet, 0, len(got))
		for _, ls := range got {
			gotLabels = append(gotLabels, ls.Labels)
		}
		assert.Len(t, gotLabels, len(expectedLabels))
		for i := range expectedLabels {
			assert.Equal(t, expectedLabels[i], gotLabels[i])
		}
	})
```
