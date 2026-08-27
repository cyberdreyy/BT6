### Title
Unauthenticated `/health`, `/health.txt`, `/readyz` endpoints leak internal component names and error details - ([File: core/web/health_controller.go])

### Summary
The health routes registered in `healthRoutes` at [1](#0-0)  are mounted on the `api` router group, which only applies rate limiting and session middleware — no authentication is required. `HealthController.Health` and `HealthController.Readyz` return a list of internal checks including component names and raw `err.Error()` output for any failing dependency, exposing internal service/dependency topology and error strings to any unauthenticated network client.

### Finding Description
`NewRouter` mounts `healthRoutes(app, api)` on the same `api` group used for authenticated routes, but the group itself is wrapped only with `rateLimiter` and `sessions.Sessions`, not `auth.Authenticate` [2](#0-1) . The `/sessions` and `/v2` routes explicitly re-wrap with `auth.Authenticate(...)` inside `sessionRoutes`/`v2Routes`, but `healthRoutes` never does [3](#0-2) .

`HealthController.Health` builds a `checks` slice from `checker.IsHealthy()`, and for each failing check it sets `Output: err.Error()` — this is the raw internal error message (e.g., DB/RPC/dependency errors) — and `Name: name`, which is the internal service/component identifier (e.g., head tracker, txm, specific chain/keystore services) [4](#0-3) . This is returned as JSON (default), or rendered as HTML/plaintext depending on `Accept` header, with `/health.txt` explicitly forcing `text/plain` [5](#0-4) . `Readyz` behaves similarly when the `full` query parameter is supplied, walking `checker.IsReady()` errors into the same `Name`/`Output` structure [6](#0-5) .

Notably, the codebase already contains a mitigation for a nearly identical concern — `PublicReadyz` was added specifically to be exposed publicly without leaking per-check details or names ("never returns per-check details regardless of query parameters, to avoid leaking internal service state on publicly reachable endpoints") [7](#0-6) . However `Health` and `Readyz` (the ones actually routed at `/health`, `/health.txt`, `/readyz`) do not have this protection and remain fully unauthenticated, so an unauthenticated attacker can enumerate internal component names and read raw error strings from any failing subsystem.

### Impact Explanation
This maps to Chainlink's "information disclosure" bounty class rather than fund loss or key compromise: an unauthenticated network attacker can learn internal service/component names (topology fingerprinting) and, when a dependency is failing, the exact error text (which can include connection details, hostnames, or other diagnostic data embedded in error messages), aiding further targeted attacks. It does not itself allow authentication bypass or fund movement, so impact is bounded to internal topology/error disclosure.

### Likelihood Explanation
No preconditions or credentials are required — a raw `GET /health`, `GET /health.txt`, or `GET /readyz?full` from any network-reachable client triggers this behavior, and it is fully repeatable at any time, including whenever a dependency is degraded (which is precisely when the leaked error content is most sensitive).

### Recommendation
Require authentication for `/health`, `/health.txt`, and `/readyz` (with `full`), or strip check names/`Output` error text from responses served to unauthenticated callers — mirroring the `PublicReadyz` boolean-only design — and only expose per-check names/errors behind `auth.Authenticate`.

### Proof of Concept
1. Using `httptest`, build the router via `NewRouter` with a test `chainlink.Application` whose `HealthChecker` reports a failing check with a distinctive error message (e.g., `errors.New("db dsn postgres://user:pass@host/db unreachable")`) and a distinctive check name (e.g., `"internal-db-service"`).
2. Issue `GET /health.txt` without any `Authorization`/session cookie.
3. Assert response status is 207 (Multi-Status) and body (via `writeTextTo`) contains both the check name `internal-db-service` and the raw error text, confirming disclosure without authentication.
4. Repeat with `GET /readyz?full` and `Accept: application/json`, asserting the JSON body's `checks[].name` and `checks[].output` fields contain the same internal name/error content.

### Citations

**File:** core/web/router.go (L77-91)
```go
	rl := config.WebServer().RateLimit()
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

**File:** core/web/router.go (L220-228)
```go
func healthRoutes(app chainlink.Application, r *gin.RouterGroup) {
	hc := HealthController{app}
	r.GET("/readyz", hc.Readyz)
	r.GET("/public-readyz", hc.PublicReadyz)
	r.GET("/health", hc.Health)
	r.GET("/health.txt", func(context *gin.Context) {
		context.Request.Header.Set("Accept", gin.MIMEPlain)
	}, hc.Health)
}
```

**File:** core/web/health_controller.go (L27-37)
```go
// PublicReadyz is a minimal readiness endpoint intended for public load balancer health checks.
// Unlike Readyz, it never returns per-check details regardless of query parameters, to avoid
// leaking internal service state on publicly reachable endpoints.
func (hc *HealthController) PublicReadyz(c *gin.Context) {
	ready, _ := hc.App.GetHealthChecker().IsReady()
	if !ready {
		c.Status(http.StatusServiceUnavailable)
		return
	}
	c.Status(http.StatusOK)
}
```

**File:** core/web/health_controller.go (L43-81)
```go
func (hc *HealthController) Readyz(c *gin.Context) {
	status := http.StatusOK

	checker := hc.App.GetHealthChecker()

	ready, errors := checker.IsReady()

	if !ready {
		status = http.StatusServiceUnavailable
	}

	c.Status(status)

	if _, ok := c.GetQuery("full"); !ok {
		return
	}

	checks := make([]presenters.Check, 0, len(errors))

	for name, err := range errors {
		status := HealthStatusPassing
		var output string

		if err != nil {
			status = HealthStatusFailing
			output = err.Error()
		}

		checks = append(checks, presenters.Check{
			JAID:   presenters.NewJAID(name),
			Name:   name,
			Status: status,
			Output: output,
		})
	}

	// return a json description of all the checks
	jsonAPIResponse(c, checks, "checks")
}
```

**File:** core/web/health_controller.go (L98-116)
```go
	checks := make([]presenters.Check, 0, len(errors))
	for name, err := range errors {
		status := HealthStatusPassing
		var output string

		if err != nil {
			status = HealthStatusFailing
			output = err.Error()
		} else if failing {
			continue // omit from returned data
		}

		checks = append(checks, presenters.Check{
			JAID:   presenters.NewJAID(name),
			Name:   name,
			Status: status,
			Output: output,
		})
	}
```
