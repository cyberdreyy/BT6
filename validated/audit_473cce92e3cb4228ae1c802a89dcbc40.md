### Title
Unauthenticated disclosure of LOOP plugin Prometheus metrics via GET /plugins/:name/metrics - ([File: core/web/loop_registry.go])

### Summary
The `loopRoutes` function registers `/plugins/:name/metrics` (and the sibling `/discovery` and pprof routes) directly on the top-level `api` router group without any `auth.Authenticate` middleware, unlike every other sensitive route in `router.go`. Any unauthenticated caller can request `GET /plugins/<name>/metrics` for any registered plugin name and receive the plugin's raw Prometheus metrics text.

### Finding Description
`NewRouter` builds an `api` group with a session middleware but registers most controllers behind explicit `auth.Authenticate(...)` wrappers (e.g. `debugRoutes`, `sessionRoutes`, `authv2` in `v2Routes`). `loopRoutes(app, api)` is called at [1](#0-0) , but the function itself attaches handlers straight to the passed-in group with no auth wrapper: [2](#0-1) . `pluginMetricHandler` looks up the plugin by name from the registry and proxies a GET to the plugin's internal `/metrics` endpoint, returning the response body verbatim to the caller with no session/role check: [3](#0-2) . The existing `TestLoopRegistry` test confirms this is reachable with a plain, unauthenticated `app.NewHTTPClient(nil)` client and expects `http.StatusOK`: [4](#0-3) . Because there is no `auth.Authenticate` call anywhere in `loopRoutes`, no session cookie or API token is required to reach `pluginMetricHandler`, `discoveryHandler`, `pluginPPROFHandler`, or `pluginPPROFPOSTSymbolHandler`.

### Impact Explanation
An unauthenticated network client can enumerate plugin names (via `/discovery`, also unauthenticated) and pull each LOOP plugin's Prometheus metrics text, which can contain internal operational counters, labels, and potentially job/feed identifiers or other operational metadata useful for reconnaissance or targeting further attacks. This matches Chainlink's bounty class of "sensitive information disclosure" from an internal/administrative endpoint that should require authentication — it does not by itself grant fund movement or key disclosure, but it is a genuine authentication-soundness violation on an internal endpoint.

### Likelihood Explanation
No credentials, role, or prior access are required — a bare HTTP GET to `/plugins/<name>/metrics` on the node's web server suffices, and the plugin name can be discovered via the equally unauthenticated `/discovery` route. This is fully reproducible and repeatable with a plain HTTP client whenever any LOOP plugin is registered and Prometheus proxying is reachable on the node's web port.

### Recommendation
Wrap `loopRoutes` (and specifically `pluginMetricHandler`, `discoveryHandler`, and the pprof handlers) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` similar to `authv2` in `v2Routes`, or otherwise restrict these internal metrics/debug endpoints to trusted network scrapers (e.g., a separate internal-only listener) rather than exposing them on the same authenticated API surface without auth checks.

### Proof of Concept
1. In a `core/web` integration test modeled on `TestLoopRegistry`, start the app, register a mock loop (`app.GetLoopRegistry().Register("mockLoopImpl")`), and start the mock Prometheus handler as in the existing test.
2. Build an HTTP client with `app.NewHTTPClient(nil)` (no login/token set) — same as the existing test already does.
3. Call `client.Get("/plugins/mockLoopImpl/metrics")` and assert `http.StatusOK` and that the response body contains the plugin's metric text (e.g., `super_great_counter 1`), with no `Cookie` or `Authorization` header set on the request.
4. Additionally assert that `client.Get("/discovery")` also returns `200` unauthenticated, confirming plugin names can be enumerated without credentials.
5. Compare against an authenticated-only route such as `/v2/config` to show the discrepancy: that route returns `401`/redirect without a session, while `/plugins/:name/metrics` returns `200`.

### Citations

**File:** core/web/router.go (L87-91)
```go
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

**File:** core/web/loop_registry_test.go (L99-140)
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

	t.Run("plugin metrics OK", func(t *testing.T) {
		t.Parallel()
		// plugin name `mockLoopImpl` matches key in PluginConfigs
		resp, cleanup := client.Get(expectedLooppEndPoint)
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("plugin metrics response %s", b)

		var (
			exceptedCount  = 1
			expectedMetric = fmt.Sprintf("%s %d", testMetricName, exceptedCount)
		)
		require.Contains(t, string(b), expectedMetric)
	})
```
