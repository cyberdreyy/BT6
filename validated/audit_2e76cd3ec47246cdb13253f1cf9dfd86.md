### Title
Unauthenticated disclosure of LOOP plugin registry, metrics, and pprof/debug endpoints - ([File: core/web/router.go])

### Summary
`loopRoutes` in `core/web/router.go` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the top-level `api` router group without any `auth.Authenticate` wrapper, unlike every other route group in `NewRouter` (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any unauthenticated network client that can reach the node's web server can enumerate loaded LOOP plugins and pull their Prometheus metrics and full pprof profiles.

### Finding Description
In `NewRouter`, `loopRoutes(app, api)` is called on the bare `api` group (line 91 of `core/web/router.go`), which only has rate-limiting and session-cookie middleware attached — no `auth.Authenticate(...)` call is applied, in contrast to `debugRoutes` (`group := r.Group("/debug", auth.Authenticate(...))`, line 181) and the `authv2`/`auth` groups used elsewhere.

`loopRoutes` itself (`core/web/router.go:230-236`) registers:
- `GET /discovery` → `loopRegistry.discoveryHandler`
- `GET /plugins/:name/metrics` → `loopRegistry.pluginMetricHandler`
- `GET /plugins/:name/debug/pprof/*profile` → `loopRegistry.pluginPPROFHandler`
- `POST /plugins/:name/debug/pprof/symbol` → `loopRegistry.pluginPPROFPOSTSymbolHandler`

with none of these routes wrapped in `auth.Authenticate` or role checks (`auth.RequiresEditRole`/`auth.RequiresAdminRole`), unlike almost every sensitive route in `v2Routes`.

Looking at the handler implementations in `core/web/loop_registry.go`:
- `discoveryHandler` (line 53) iterates `l.registry.List()` and returns, in JSON, every registered plugin's name and the internal discovery hostname/port/metrics-path — directly enumerating loaded plugin topology (`core/web/loop_registry.go:53-81`).
- `pluginMetricHandler` (line 96) proxies to `http://{loopHostName}:{p.EnvCfg.PrometheusPort}/metrics` for an attacker-supplied `:name`, returning that plugin's raw Prometheus metrics — internal state disclosure.
- `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler` (lines 150-215) proxy arbitrary pprof profile types (heap, goroutine, cmdline, trace, symbol) from the plugin's internal debug endpoint straight through to the caller, with a caller-controlled `seconds` query parameter controlling CPU profile duration — this can dump internal memory/goroutine state (potentially containing addresses, in-flight secrets, or internal RPC endpoints) to an unauthenticated caller.

Because the whole `loopRoutes` group is missing the `auth.Authenticate` middleware that protects every comparable debug/internal endpoint (`debugRoutes`), no session, API token, or role is required to hit any of these paths.

### Impact Explanation
An unauthenticated attacker who can reach the node's HTTP listener can enumerate all loaded LOOP plugins and their internal metrics/pprof endpoints, disclosing internal plugin topology, Prometheus metrics, and runtime memory/goroutine dumps. This aids further attacks (e.g., identifying plugin versions/behavior, extracting in-memory data via heap/goroutine profiles) and constitutes an information-disclosure vulnerability against the node's internal architecture — matching the "internal plugin topology disclosure aiding further attacks" impact class raised in the question.

### Likelihood Explanation
No credentials, session, or role are required — a bare `GET /discovery` or `GET /plugins/<name>/debug/pprof/heap` from any network client that can reach the node's API port is sufficient. This is trivially repeatable and requires no preconditions beyond network reachability to the node's web server, which is the same reachability required for the documented `/health` and `/v2/*` routes.

### Recommendation
Wrap `loopRoutes` registration with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware (and appropriate role checks, e.g. `auth.RequiresAdminRole` for pprof/debug endpoints) used by `debugRoutes` and `v2Routes`, so these internal plugin-registry/debug endpoints require at least a valid session/API token and admin role before being reachable.

### Proof of Concept
Add a handler-level test in `core/web` (alongside `loop_registry_internal_test.go` or a new `loop_registry_test.go` using `cltest.NewApplication`/`web.NewRouter`):
1. Build the router via `NewRouter(app, nil)` with a test app that has a `LoopRegistry` containing at least one registered plugin.
2. Issue `httptest` requests with no `Authorization`/session cookie:
   - `GET /discovery` → currently expect 200 with JSON plugin list; assert should be 401/403.
   - `GET /plugins/<name>/metrics` → currently 200 (or 500 on proxy failure) without auth; assert should be 401/403.
   - `GET /plugins/<name>/debug/pprof/heap` → currently reachable without auth; assert should be 401/403.
   - `POST /plugins/<name>/debug/pprof/symbol` → currently reachable without auth; assert should be 401/403.
3. Repeat with a valid session cookie for a `view`-role user and confirm behavior against the intended role requirement (e.g., admin-only) once the fix is applied.
4. Repeat with a valid admin session/API token and assert 200 to confirm the fix does not break legitimate scraping/debugging use cases. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** core/web/loop_registry.go (L53-81)
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
