I have enough evidence to answer definitively.

### Title
Unauthenticated exposure of internal LOOP plugin metrics and pprof endpoints, with no role-based or plugin-sensitivity differentiation - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` routes handled by `LoopRegistryServer.pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` are registered without any authentication or role-check middleware, unlike essentially every other sensitive route in the router. This means any unauthenticated client can pull Prometheus metrics and full pprof profiles (heap, goroutine, profile, trace, symbol) from any registered LOOP plugin by name — including plugins like `vault` or a DKG-related LOOP — with zero differentiation from a benign plugin like `median`.

### Finding Description
In `core/web/router.go`, `loopRoutes` is invoked directly on the base `api` group, which only carries rate-limiting and session middleware: [1](#0-0) 
Compare this to `debugRoutes`, which explicitly wraps its `/debug/vars` route in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`: [2](#0-1) 
and to `v2Routes`, where nearly every sensitive route is wrapped in `authv2` (`auth.Authenticate(...)`) plus a role wrapper such as `auth.RequiresEditRole`, `auth.RequiresRunRole`, or `auth.RequiresAdminRole` (e.g. the vault DKG routes): [3](#0-2) 

`loopRoutes` itself registers all four handlers with no `auth.Authenticate(...)` and no `auth.RequiresXRole(...)` wrapper at all: [4](#0-3) 

Inside `pluginMetricHandler`, the only check performed is whether the plugin name exists in the registry (`l.registry.Get(pluginName)`); there is no inspection of plugin name/sensitivity, no session/token check, and no role comparison before proxying the request to the plugin's internal metrics endpoint and returning the raw body to the caller: [5](#0-4) 

The pprof handlers behave identically — looking up the plugin by name and forwarding to `/debug/pprof/...` on the internal LOOP host, again with no authentication or authorization gate: [6](#0-5) 

Because these routes are mounted straight on `api` (session + rate-limit only, no `auth.Authenticate`), any unauthenticated HTTP client that can reach the node's web server can enumerate registered plugin names (via the also-unauthenticated `/discovery` route, which lists all registered plugins including their names) and then pull `/plugins/<name>/metrics` or `/plugins/<name>/debug/pprof/heap` for any plugin — sensitive (vault/DKG) or not — with identical, fully open access. There is no per-route role declaration to enforce a minimum role, and consequently no mechanism exists to differentiate a sensitive plugin from a benign one.

### Impact Explanation
This is an authentication bypass / information disclosure vulnerability: an unauthenticated attacker can retrieve full runtime metrics and pprof heap/goroutine dumps for any LOOP plugin, including potentially sensitive plugins such as `vault` or `dkg`, without any credential. Metrics and heap dumps of vault/DKG LOOPs can leak internal state, memory contents, or operational details useful for further attacks against key material or DON coordination, matching a "sensitive data exposure via authentication/authorization bypass" bounty class.

### Likelihood Explanation
No credentials, roles, or tokens are required — the attacker only needs network access to the node's HTTP API port, which is the same precondition as any other unauthenticated route access. The `/discovery` endpoint (also unauthenticated) trivially enumerates plugin names, making exploitation fully repeatable and requiring no special preconditions beyond a running node with registered LOOP plugins.

### Recommendation
Wrap `loopRoutes` registrations in an authenticated group (e.g. `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`), and apply an appropriate minimum role (at least `auth.RequiresViewRole` for metrics, higher for pprof given its resource/DoS and information-disclosure risk). Additionally consider an explicit sensitivity classification for plugin names (e.g. vault/DKG) requiring `auth.RequiresAdminRole` or a dedicated ACL, rather than relying on a single uniform role for all plugins.

### Proof of Concept
1. In a `core/web` router test (using `httptest` and `NewRouter`/`loopRoutes` setup), register a `plugins.LoopRegistry` entry with `Name: "vault"` (or `"dkg"`) pointing to a local test HTTP server exposing `/metrics` and `/debug/pprof/heap`.
2. Issue an unauthenticated `GET /plugins/vault/metrics` request (no session cookie, no API token header) directly against the router/engine.
3. Assert the response is `200 OK` with the proxied metrics body returned, identical to the behavior for a plugin named `"median"`.
4. Repeat with `GET /plugins/vault/debug/pprof/heap` and assert `200 OK` with pprof data returned.
5. Contrast with a call to an authenticated route (e.g. `GET /v2/keys/eth` or `POST /vault/dkg_results/verify`) without credentials, asserting `401 Unauthorized`, to demonstrate the inconsistency: plugin routes bypass the authentication layer entirely while comparable sensitive routes correctly enforce it.

### Citations

**File:** core/web/router.go (L86-92)
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

**File:** core/web/router.go (L441-446)
```go
		vault := VaultController{app}
		authv2.POST("/vault/dkg_results/verify", auth.RequiresEditRole(vault.VerifyDKGResult))
		authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
