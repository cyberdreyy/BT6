### Title
Unauthenticated `/readyz?full` discloses raw internal health-check error strings - ([File: core/web/router.go], [File: core/web/health_controller.go])

### Summary
`healthRoutes` registers `GET /readyz` on the top-level `api` router group, which only has rate-limiting and session middleware — no `auth.Authenticate` wrapper is applied. `HealthController.Readyz`, when called with the `full` query parameter, iterates every registered `services.HealthReporter`'s error and puts the raw `err.Error()` string into the `Output` field of the JSON:API response with no redaction, so any error text bubbled up from a subservice (DB, RPC pool, gateway connector, etc.) is returned verbatim to the caller.

### Finding Description
`healthRoutes` wires `/readyz` with zero auth: `r.GET("/readyz", hc.Readyz)` [1](#0-0) , and this group inherits only rate limiting/session middleware from `NewRouter`, not `auth.Authenticate` [2](#0-1) . In `Readyz`, when `?full` is present, every check's error is placed unmodified into `presenters.Check.Output` via `err.Error()` and returned through `jsonAPIResponse` [3](#0-2) . There is no allow-list, sanitization, or truncation of the error text — whatever a `HealthReporter` implementation returns as its error is echoed straight into the HTTP response body to an unauthenticated caller.

Notably, the codebase already contains an explicit, purpose-built mitigation for this exact concern: `PublicReadyz`, whose doc comment states it "never returns per-check details regardless of query parameters, to avoid leaking internal service state on publicly reachable endpoints" [4](#0-3) . This confirms the maintainers are aware that `Readyz` output can leak internal state, and intended `/readyz` to be used only on internal/trusted networks while `/public-readyz` is the safe option for public exposure — `/readyz` remains unauthenticated by design in the router, identical in exposure level to the pre-existing `/health` endpoint [5](#0-4) .

I could not find, within the indexed portion of the codebase, any concrete `HealthReporter.Ready()`/`HealthReport()` implementation whose error message embeds a secret (e.g., a DB DSN or RPC URL with an API key). The `Checker`/`HealthReporter` interfaces are generic pass-throughs from `chainlink-common/pkg/services` [6](#0-5) , and the actual error strings returned by concrete services (DB pool, EVM RPC client, gateway connector, etc.) were not fully resolvable through search — this is a real limitation of the available index, not a confirmed absence of secret-bearing errors.

### Impact Explanation
If any registered service's health-check error string embeds sensitive internal details (connection strings, internal hostnames, RPC endpoint URLs, occasionally credentials embedded in a URL), an unauthenticated attacker with only network access to the node's API port can retrieve them via a single unauthenticated GET request. This matches the "information disclosure of internal service details" class, but is bounded — it discloses *error text*, not private keys, session tokens, or job data, and only when a check is actively failing.

### Likelihood Explanation
No credentials are required — pure network reachability to `/readyz` is the only precondition, and this is trivially repeatable. However, real-world severity depends entirely on whether a concrete health-check error ever includes a genuinely sensitive string, which was not confirmed here, and on whether the node operator has followed the documented practice of exposing `/public-readyz` (not `/readyz`) to untrusted networks. The router itself does not prevent public exposure of `/readyz`, but the repository already flags this as a known risk pattern (via `PublicReadyz`'s existence and doc comment) rather than treating it as an unaddressed bug — the same unauthenticated, full-detail behavior has existed on `/health` as well.

### Recommendation
Apply authentication (or at minimum restrict to loopback/internal callers) to `/readyz?full` and `/health` detailed output, or redact/generalize error strings before placing them into `presenters.Check.Output` (e.g., map internal errors to a fixed set of non-sensitive status strings) so that no subservice implementation detail can leak regardless of future changes to error messages.

### Proof of Concept
Go handler-level test in `core/web/health_controller_test.go`:
1. Build a `mocks.Checker` whose `IsReady()` returns `(false, map[string]error{"db": errors.New("failed to connect: postgres://user:secretpass@10.0.0.5:5432/db")})`.
2. Start the app via `cltest.NewApplicationWithKey` with this mocked `HealthChecker`, without any auth headers on the HTTP client (`app.NewHTTPClient(nil)`).
3. `client.Get("/readyz?full=true")` and assert `resp.StatusCode == 503`.
4. Read body, unmarshal JSON:API `checks` array, and assert `checks[0].Output` contains the literal string `"secretpass"` — demonstrating the error text reaches an unauthenticated caller unmodified.
5. Contrast with a parallel assertion against `/public-readyz?full=true` returning an empty body, confirming the asymmetry between the two endpoints (as already partly covered by `TestHealthController_PublicReadyz`) [7](#0-6) .

### Citations

**File:** core/web/router.go (L76-90)
```go
	engine.Use(helmet.Default())
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

**File:** core/web/health_controller.go (L56-80)
```go
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
```

**File:** core/services/health.go (L9-32)
```go
	"github.com/pkg/errors"

	"github.com/smartcontractkit/chainlink-common/pkg/services"
	"github.com/smartcontractkit/chainlink/v2/core/logger"
)

var _ Checker = (*services.HealthChecker)(nil)

// Checker provides a service which can be probed for system health.
type Checker interface {
	// Register a service for health checks.
	Register(service services.HealthReporter) error
	// Unregister a service.
	Unregister(name string) error
	// IsReady returns the current readiness of the system.
	// A system is considered ready if all checks are passing (no errors)
	IsReady() (ready bool, errors map[string]error)
	// IsHealthy returns the current health of the system.
	// A system is considered healthy if all checks are passing (no errors)
	IsHealthy() (healthy bool, errors map[string]error)

	Start() error
	Close() error
}
```

**File:** core/web/health_controller_test.go (L61-110)
```go
func TestHealthController_PublicReadyz(t *testing.T) {
	t.Parallel()
	var tt = []struct {
		name   string
		ready  bool
		status int
	}{
		{
			name:   "not ready",
			ready:  false,
			status: http.StatusServiceUnavailable,
		},
		{
			name:   "ready",
			ready:  true,
			status: http.StatusOK,
		},
	}
	for _, tc := range tt {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			app := cltest.NewApplicationWithKey(t)
			healthChecker := new(mocks.Checker)
			healthChecker.On("Start").Return(nil).Once()
			healthChecker.On("IsReady").Return(tc.ready, nil)
			healthChecker.On("Close").Return(nil).Once()

			app.HealthChecker = healthChecker
			require.NoError(t, app.Start(t.Context()))

			client := app.NewHTTPClient(nil)

			// Base path returns status only, no body.
			resp, cleanup := client.Get("/public-readyz")
			t.Cleanup(cleanup)
			assert.Equal(t, tc.status, resp.StatusCode)
			body, err := io.ReadAll(resp.Body)
			require.NoError(t, err)
			assert.Empty(t, body)

			// ?full=true must NOT expose per-check details on this endpoint.
			respFull, cleanupFull := client.Get("/public-readyz?full=true")
			t.Cleanup(cleanupFull)
			assert.Equal(t, tc.status, respFull.StatusCode)
			bodyFull, err := io.ReadAll(respFull.Body)
			require.NoError(t, err)
			assert.Empty(t, bodyFull)
		})
	}
}
```
