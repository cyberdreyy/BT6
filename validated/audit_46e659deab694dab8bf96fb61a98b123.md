### Title
Missing role-based authorization on `/debug/vars` allows any authenticated session user (including view-role) to read Go runtime internals - ([File: core/web/router.go])

### Summary
The `debugRoutes` function wires `/debug/vars` behind only `auth.Authenticate(..., auth.AuthenticateBySession)` with no subsequent `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrapper, unlike virtually every other sensitive endpoint in `v2Routes`. Any user with a valid session cookie, regardless of role (`view`, `run`, `edit`, `admin`), can hit the standard Go `expvar` handler and read process command-line arguments and `memstats` (heap/GC internals).

### Finding Description
`debugRoutes` in [1](#0-0)  creates a route group protected only by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`, then registers `group.GET("/vars", expvar.Handler())`. Compare this to the rest of `v2Routes`, where nearly every mutating or sensitive read endpoint is wrapped with an explicit role check such as `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole` (e.g. [2](#0-1) , [3](#0-2) ).

`auth.AuthenticateBySession` in [4](#0-3)  only validates that the session cookie maps to a valid, currently-logged-in user of any role — it sets `SessionUserKey` to whatever `clsessions.User` role that account has, without any role gating. Since `debugRoutes` never wraps the handler with a `Requires*Role` function, a `view`-role user's session (the lowest privilege tier, defined as read-only in `clsessions.UserRoleView`) is sufficient to reach `expvar.Handler()`. Go's built-in `expvar` package by default publishes `cmdline` (process command-line, which can reveal file paths, flags and possibly other environment-derived startup parameters) and `memstats` (detailed GC/heap statistics), both of which are process/runtime internals not exposed elsewhere in the authenticated API to view-role users.

This is a genuine authorization-exactness gap: the intended privilege model in this codebase reserves administrative/debug-level information for `edit`/`run`/`admin` roles, but `/debug/vars` was left with authentication-only gating.

### Impact Explanation
The impact is limited to information disclosure of Go runtime internals (`cmdline`, `memstats`) to any authenticated user regardless of role — there is no direct fund movement, key disclosure, or write capability here. This maps to a low-severity "internal information disclosure to a minimally privileged user" class rather than a critical/high impact, since `expvar` does not expose secrets, private keys, or other users' data by default, but does aid an attacker in fingerprinting the node's runtime state (heap size, GC pressure, command-line invocation) for follow-on reconnaissance.

### Likelihood Explanation
Precondition is only a valid `view`-role session cookie, which is the lowest privilege tier a node operator can grant. No token or elevated role is required, and the request is a single unauthenticated-in-role `GET /debug/vars`; this is trivially repeatable by any view-role user or by an attacker who compromises/social-engineers a view-role credential.

### Recommendation
Wrap the `/debug/vars` route with an explicit role check consistent with the rest of the authenticated API, e.g. `group.GET("/vars", auth.RequiresAdminRole(...))` or at minimum `auth.RequiresEditRole`, so that only sufficiently privileged accounts can access runtime debug data.

### Proof of Concept
1. In a `core/web` handler-integration test (following the pattern in `core/web/router_test.go` / existing controller tests that set up a test app with `setupJSONAPIWithVersioning`-style helper and a session cookie fixture), create a user with `clsessions.UserRoleView` and log in to obtain a session cookie.
2. Issue `GET /debug/vars` with that session cookie attached.
3. Assert the response status is `200 OK` and the body is valid JSON containing keys `cmdline` and `memstats` (standard `expvar` output).
4. As a control, assert that other debug-adjacent endpoints (e.g. `/v2/log` PATCH which is admin-gated) return `401`/`403` for the same view-role session, demonstrating the inconsistency: `/debug/vars` should behave the same way but currently does not.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L251-254)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
```

**File:** core/web/router.go (L316-320)
```go
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/auth/auth.go (L55-71)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```
