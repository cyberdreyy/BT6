### Title
`/debug/vars` endpoint lacks role-based authorization, allowing any authenticated session (including lowest view-role) to read internal runtime state - ([File: core/web/router.go])

### Summary
The `debugRoutes` function wires `/debug/vars` behind only `auth.AuthenticateBySession`, with no `auth.RequiresAdminRole` (or any role) wrapper, unlike every other sensitive/administrative route in the router. Any user holding a valid session cookie — including the lowest-privilege "view" role — can call this route and receive the raw `expvar.Handler()` output.

### Finding Description
In `debugRoutes`, the route is registered as:
```go
group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
group.GET("/vars", expvar.Handler())
``` [1](#0-0) 

`auth.Authenticate(..., auth.AuthenticateBySession)` only validates that a session cookie corresponds to a logged-in user; it performs no role check. Contrast this with the rest of `v2Routes`, where nearly every administrative or state-exposing handler is wrapped with `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole` (e.g. key export/import, user management, config-changing endpoints) [2](#0-1) [3](#0-2) . `/debug/vars` is the only authenticated-but-role-unchecked route serving Go's built-in `expvar` package, which exposes `runtime.MemStats`, `cmdline` (process command-line arguments), and any custom-registered `expvar.Var`s process-wide.

Attack flow: an unprivileged, view-only user (the lowest role in the system) logs in normally, obtains a valid session cookie via `/sessions`, then issues `GET /debug/vars` with that cookie. `auth.AuthenticateBySession` accepts it since it only checks session validity, and the handler runs `expvar.Handler()` directly without any subsequent role gate, returning full JSON of registered vars to the view-role caller.

### Impact Explanation
This is an authorization/least-privilege gap: a view-role user gets access equivalent to an admin for this specific endpoint, and receives internal runtime telemetry (memory stats, command-line invocation, and any registered internal counters/vars) that is not restricted to admins elsewhere in the codebase. This falls into the "information disclosure of internal state to a lower-privileged principal" class — it does not directly disclose private keys or secrets (`expvar` by default does not register cryptographic material), but it does violate the intended minimum-role model enforced everywhere else in the router and can aid reconnaissance for further attacks.

### Likelihood Explanation
Trivial and fully reproducible: any user with any valid session (view role is sufficient, no special token or elevated role needed) can hit `GET /debug/vars` and get a 200 response with the full expvar payload. No timing, race conditions, or additional preconditions apply.

### Recommendation
Wrap the `/debug/vars` handler with the same role-based middleware used elsewhere for sensitive endpoints, e.g. `auth.RequiresAdminRole(...)`, restricting it to admin-role sessions only, consistent with the rest of the router's minimum-role model.

### Proof of Concept
Go handler-level integration test plan (analogous to existing router tests, e.g. `core/web/router_test.go` patterns):
1. Build the router via `NewRouter` with a test `chainlink.Application`.
2. Create a session for a user with `sessions.UserRoleView` (lowest role) and obtain the session cookie via `POST /sessions`.
3. Issue `GET /debug/vars` with that cookie attached.
4. Assert current (buggy) behavior: response status is `200 OK` and body contains expvar JSON keys (`cmdline`, `memstats`).
5. Assert expected/fixed behavior: response status should be `403 Forbidden` for view-role sessions, matching the behavior of other `auth.RequiresAdminRole`-wrapped routes when tested with the same view-role session (e.g. compare against `GET /v2/users` which correctly returns 403 for non-admin sessions).

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L251-256)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
```

**File:** core/web/router.go (L318-320)
```go
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```
