This confirms the behavior described: `debugRoutes` only wraps `/debug/vars` with `auth.Authenticate(..., auth.AuthenticateBySession)`, with no role-check wrapper (`RequiresAdminRole`, `RequiresEditRole`, or `RequiresRunRole`) applied afterward [1](#0-0) . `AuthenticateBySession` only validates that the session cookie corresponds to a valid, non-expired user session and sets the user object into context — it performs no role check at all [2](#0-1) . By contrast, `/v2/users` and most other sensitive routes are explicitly wrapped with `auth.RequiresAdminRole(...)`, `RequiresEditRole(...)`, or `RequiresRunRole(...)`, which extract the authenticated user and reject unless role is sufficient [3](#0-2) [4](#0-3) .

However, per the audit rules, this is a **role/authorization exactness** issue that only affects the boundary between "view" and "admin" roles among already-authenticated node operator users — not an unprivileged/unauthenticated attacker. The task explicitly requires the attacker to be unprivileged: "an unauthenticated client of the node API or gateway, a view/run-role user, a restricted API token or external-initiator credential holder..." A view-role user (holding a valid session) does qualify as an in-scope attacker persona, so this needs deeper consideration.

Given the precondition is "hold a valid session or API token" with view-role, and the impact is disclosure of Go runtime `expvar` data (command-line args, memstats, and any custom-registered vars) to that view-role user rather than to a fully external/unauthenticated party — this is a real role-boundary bypass: a view-role user (lowest privilege, meant only for read-only dashboards, no key/secret access) can hit an endpoint gated at "any authenticated user" instead of "admin only," disclosing internal runtime state not restricted to their role.

### Title
Missing role-gate on `/debug/vars` allows any authenticated (including view-role) user to dump Go runtime variables - (File: core/web/router.go)

### Summary
The `/debug/vars` route registered in `debugRoutes` is protected only by `auth.Authenticate(..., auth.AuthenticateBySession)`, which validates session identity but performs no role check, unlike other sensitive endpoints (e.g. `/v2/users`) that are wrapped with `auth.RequiresAdminRole`. As a result, any authenticated user — including one with the lowest `view` role — can access the standard library `expvar` handler and dump internal Go runtime variables.

### Finding Description
`debugRoutes` in `core/web/router.go` mounts `GET /debug/vars` to `expvar.Handler()` behind a group gated only by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [1](#0-0) . `AuthenticateBySession` reads the session cookie, resolves the session ID to a `clsessions.User`, and simply sets that user into the gin context without inspecting `user.Role` [5](#0-4) . There is no subsequent `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` wrapper applied to this route, whereas comparable sensitive routes such as `/v2/users` are explicitly wrapped in `auth.RequiresAdminRole(uc.Index)` [3](#0-2) , and `RequiresAdminRole` explicitly checks `user.Role != clsessions.UserRoleAdmin` before invoking the handler [4](#0-3) . Consequently, any user with a valid session cookie — regardless of role (`view`, `run`, `edit`, or `admin`) — can successfully call `GET /debug/vars` and receive the full `expvar` dump (command-line, memstats, and any custom-registered exported vars).

### Impact Explanation
This maps to a "sensitive information disclosure" impact: a low-privilege `view`-role session (intended only for read-only dashboards) can retrieve internal Go process runtime metadata via `expvar`, which is a broader disclosure surface than that role should have. Depending on what is registered via `expvar.Publish` elsewhere in the codebase, this could expose internal configuration or state not otherwise available to `view`-role users. This is a role-authorization exactness bug rather than a full authentication bypass, since it still requires possessing valid credentials of at least the lowest privilege tier.

### Likelihood Explanation
Requires only a valid `view`-role session cookie or equivalent minimal credential — a precondition trivially achievable by any operator-issued low-privilege account. No further preconditions (e.g., admin token, secret) are needed, and the request is a simple unauthenticated-role `GET /debug/vars` call, fully repeatable.

### Recommendation
Wrap the `/debug/vars` route with `auth.RequiresAdminRole` (matching the pattern used for `/v2/users` and other admin-sensitive endpoints), e.g.:
```go
group.GET("/vars", auth.RequiresAdminRole(func(c *gin.Context) { expvar.Handler()(c) }))
```

### Proof of Concept
1. Set up a `gin` test router calling `debugRoutes(app, apiGroup)` with a stub `AuthenticationProvider` that returns a `clsessions.User{Role: clsessions.UserRoleView}` for a given session ID via `AuthorizedUserWithSession`.
2. Create an `httptest.NewRecorder()` request `GET /debug/vars` with a session cookie encoding that session ID (matching what `sessions.Sessions(auth.SessionName, sessionStore)` middleware expects).
3. Serve the request through the router and assert `resp.Code == http.StatusOK` and that the body contains expvar JSON (e.g., `"cmdline"` key) — demonstrating that a `view`-role session obtains the dump.
4. As a contrast/control test, hit `GET /v2/users` with the same `view`-role session and assert `resp.Code == http.StatusForbidden`, confirming the inconsistency between the two routes' authorization gates.

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

**File:** core/web/auth/auth.go (L52-71)
```go
// AuthenticateBySession authenticates the request by the session cookie.
//
// Implements authMethod
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

**File:** core/web/auth/auth.go (L238-255)
```go
// RequiresAdminRole extracts the user object from the context, and asserts the user's role is 'admin'
func RequiresAdminRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role != clsessions.UserRoleAdmin {
			c.Abort()
			addForbiddenErrorHeaders(c, "admin", string(user.Role), user.Email)
			jsonAPIError(c, http.StatusForbidden, errors.New("Forbidden"))
			return
		}
		handler(c)
	}
}
```
