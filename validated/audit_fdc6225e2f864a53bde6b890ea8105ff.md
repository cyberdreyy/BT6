### Title
Missing role gate on GET /debug/vars allows view/run-role users to read internal expvar runtime data - ([File: core/web/router.go])

### Summary
`debugRoutes` registers `GET /debug/vars` behind `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` only, without wrapping the handler in `auth.RequiresEditRole` or `auth.RequiresAdminRole` as is done consistently for every sensitive route elsewhere in `v2Routes`. Any authenticated user, including one with only the `view` role, can therefore reach the `expvar.Handler()` and dump internal process variables.

### Finding Description
`debugRoutes` builds a route group scoped only by session/token authentication middleware, with no subsequent role check: [1](#0-0) 

Contrast this with every other authenticated route in `v2Routes`, where handlers are additionally wrapped with `auth.RequiresEditRole(...)`, `auth.RequiresAdminRole(...)`, or `auth.RequiresRunRole(...)` on top of the same `auth.Authenticate` middleware, e.g.: [2](#0-1) [3](#0-2) 

Because `auth.Authenticate(..., auth.AuthenticateBySession)` only validates that a session cookie belongs to a logged-in user — it does not check the user's `Role` field at all — a user provisioned with the lowest privilege (`view`) satisfies this middleware exactly the same as an `admin` user would. The request then falls straight through to `expvar.Handler()`, which serves the full expvar registry (`memstats`, `cmdline`, and any custom-registered expvars) as JSON with no additional authorization check inside the handler itself.

### Impact Explanation
This is an authorization-bypass / role-gate omission: a low-privilege (`view`) authenticated user obtains data intended to be restricted to elevated (edit/admin) operators. The `expvar` output can leak internal runtime state (goroutine/memory stats, command-line arguments, and any custom vars registered by the node or its plugins), which is inconsistent with the router's established privilege model (all sensitive introspection/config endpoints elsewhere require edit or admin role). This maps to Chainlink's "sensitive information disclosure via inconsistent/broken access control" bounty class rather than direct fund loss, since expvar itself does not expose private keys or secrets by default, but it does violate the documented authorization-exactness invariant.

### Likelihood Explanation
Precondition is minimal: possession of a valid session cookie for any user account, even one restricted to the `view` role (no admin/edit privileges, no host/database access). The exploit is a single unauthenticated-of-privilege `GET /debug/vars` request after login — fully repeatable, deterministic, and requires no race condition or special timing.

### Recommendation
Wrap the `/debug/vars` handler with the same role-gating middleware used elsewhere for administrative/introspection endpoints, e.g. `auth.RequiresAdminRole(...)` (or at minimum `auth.RequiresEditRole(...)`), consistent with how `metricRoutes` and other authv2 endpoints are protected:
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
    group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
    group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) {
        expvar.Handler().ServeHTTP(c.Writer, c.Request)
    }))
}
```

### Proof of Concept
Go handler-level integration test plan (in `core/web/router_test.go` or similar, using the existing test harness that logs in test users with configurable roles):
1. Set up a test app/router via the existing `setupWebApp`/`sessionsController`-style test helper used in other router tests.
2. Create a user with `sessions.UserRoleView` and authenticate to obtain a valid session cookie (mirroring how other role-restriction tests, e.g. for `/v2/keys/eth` POST, log in a view-role user and expect 403).
3. Issue `GET /debug/vars` with that session cookie.
4. Assert: current behavior returns `200 OK` with a JSON body containing `memstats`; expected/fixed behavior should return `403 Forbidden` for the `view` role, matching the pattern used for `auth.RequiresEditRole`/`auth.RequiresAdminRole`-protected routes in `core/web/router_test.go`.
5. Repeat with an `admin`/`edit` role user and assert `200 OK` continues to work post-fix, confirming the fix does not break legitimate access.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L298-303)
```go
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
		lcaC := LCAController{app}
		authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))
		lpSkipC := LPSkipController{app}
		authv2.POST("/lp_skip_to_block", auth.RequiresRunRole(lpSkipC.LPSkipToBlock))

```

**File:** core/web/router.go (L316-320)
```go
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```
