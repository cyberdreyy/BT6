Confirmed: `core/sessions/user.go` defines the four roles (`UserRoleAdmin`, `UserRoleEdit`, `UserRoleRun`, `UserRoleView`), consistent with `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrappers used elsewhere in `core/web/router.go`. The pprof routes are the only sensitive debug endpoints in `metricRoutes` lacking any such role wrapper.

### Title
Missing role-based authorization on pprof debug endpoints allows any authenticated non-admin user (view/run/edit role token) to dump heap memory - ([File: core/web/router.go])

### Summary
`metricRoutes` registers `/debug/pprof/*` handlers (including `/heap`, `/goroutine`, `/allocs`, `/profile`, `/trace`) under the `authv2` group in `v2Routes`, which only requires passing `auth.Authenticate` (session or API token) with no role check. Unlike every other sensitive endpoint in the same file (e.g. `authv2.PATCH("/log", auth.RequiresAdminRole(...))`, `authv2.GET("/users", auth.RequiresAdminRole(...))`), the pprof group is not wrapped with `auth.RequiresAdminRole`, so a user holding only a `view` or `run` role API token can retrieve a full heap dump.

### Finding Description
In `core/web/router.go`, `v2Routes` builds `authv2` as:
```go
authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
    auth.AuthenticateByToken,
    auth.AuthenticateBySession,
))
``` [1](#0-0) 
This only authenticates the caller (i.e. confirms they hold *a* valid token/session), it does not check role. Every other sensitive action registered on `authv2` explicitly wraps its handler with a role check, e.g. `auth.RequiresAdminRole`, `auth.RequiresEditRole`, or `auth.RequiresRunRole`: [2](#0-1) [3](#0-2) 

However, `metricRoutes(authv2)` is called at the end of the group with no additional role wrapper: [4](#0-3) 

And `metricRoutes` itself applies no `auth.RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` to any of the pprof handlers: [5](#0-4) 

The role-checking middlewares are defined in `core/web/auth/auth.go`: `RequiresRunRole` (allows any role, only rejects unauthenticated), `RequiresEditRole` (rejects `view`/`run`), and `RequiresAdminRole` (only allows `admin`): [6](#0-5) 

Since pprof has none of these, any of the four roles (`admin`, `edit`, `run`, `view`) — defined in `core/sessions/user.go` — can hit `GET /v2/debug/pprof/heap` and any other pprof sub-route once authenticated with any valid API token or session, regardless of role.

The existing test `TestRBAC_Routemap_Admin` in `core/web/auth/auth_test.go` only verifies that admin-role requests to listed routes do not return 401/403; it does not include `/v2/debug/pprof/*` in `routesRolesMap`, and there is no negative test asserting that non-admin roles are rejected from pprof routes. [7](#0-6) 

### Impact Explanation
`pprof.Handler("heap")` and similar profiling handlers dump raw in-process memory content (heap objects, goroutine stacks, active profiles), which in a Chainlink node can include decrypted key material, DB credentials, or other sensitive in-memory state. Any credential holder with the lowest privilege role (`view`) can exfiltrate this data by simply calling the pprof endpoints, which is a clear scoped disclosure of sensitive data to an under-privileged principal — matching the "unauthorized information disclosure of secrets/keys" bounty impact class, since these routes are otherwise intended to be operator/admin-only debug tooling (as evidenced by every comparable sensitive endpoint requiring `RequiresAdminRole`/`RequiresEditRole`).

### Likelihood Explanation
The only precondition is possession of any valid, currently active API token or session — even the lowest-privilege `view` role suffices, since `auth.Authenticate` does not distinguish roles and no role wrapper is applied to the pprof group. This is trivially and repeatably exploitable: a single authenticated `GET /v2/debug/pprof/heap` request succeeds. No additional timing, race conditions, or special configuration is required.

### Recommendation
Wrap the pprof route group with `auth.RequiresAdminRole` (or at minimum `auth.RequiresEditRole`) consistent with other sensitive endpoints, e.g. change `metricRoutes(authv2)` registration so each handler in `metricRoutes` is wrapped: `pprofGroup.GET("/heap", auth.RequiresAdminRole(ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP)))`, and similarly for the other pprof sub-routes and `/debug/vars` if not already restricted.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/auth/auth_test.go` style):
1. Start app via `cltest.NewApplicationEVMDisabled(t)`, `web.Router(t, app, nil)`, `httptest.NewServer(router)`.
2. Create a user/API token with `UserRoleView` (or `UserRoleRun`) via the sessions/ORM test helpers (mirroring how `NewHTTPClient` is parameterized with role in other RBAC tests).
3. Issue `client.Get("/v2/debug/pprof/heap")` (and `/v2/debug/pprof/goroutine`, `/v2/debug/pprof/allocs`) using the view-role-authenticated client.
4. Assert `resp.StatusCode == http.StatusForbidden` (expected fix behavior) — currently the test would show `resp.StatusCode == http.StatusOK` with a non-empty heap dump body, demonstrating the vulnerability.
5. Repeat with an admin-role client and assert `http.StatusOK`, confirming the fix preserves legitimate admin access.

### Citations

**File:** core/web/router.go (L185-199)
```go
func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	pprofGroup.POST("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/trace", ginHandlerFromHTTP(pprof.Trace))
	pprofGroup.GET("/allocs", ginHandlerFromHTTP(pprof.Handler("allocs").ServeHTTP))
	pprofGroup.GET("/block", ginHandlerFromHTTP(pprof.Handler("block").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/mutex", ginHandlerFromHTTP(pprof.Handler("mutex").ServeHTTP))
	pprofGroup.GET("/threadcreate", ginHandlerFromHTTP(pprof.Handler("threadcreate").ServeHTTP))
}
```

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L251-257)
```go
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)
```

**File:** core/web/router.go (L411-412)
```go
		authv2.GET("/log", lgc.Get)
		authv2.PATCH("/log", auth.RequiresAdminRole(lgc.Patch))
```

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** core/web/auth/auth_test.go (L343-384)
```go
// The following test implementations work by asserting only that "Unauthorized/Forbidden" errors are not returned (success case),
// because hitting the handler are not mocked and will crash as expected
// Iterate over the above routesRolesMap and assert each path is wrapped and
// the user role is enforced with the correct middleware
func TestRBAC_Routemap_Admin(t *testing.T) {
	t.Parallel()
	app := cltest.NewApplicationEVMDisabled(t)
	require.NoError(t, app.Start(t.Context()))

	router := web.Router(t, app, nil)
	ts := httptest.NewServer(router)
	defer ts.Close()

	// Assert all admin routes
	// no endpoint should return StatusUnauthorized
	client := app.NewHTTPClient(nil)
	for _, route := range routesRolesMap {
		func() {
			var resp *http.Response
			var cleanup func()

			switch route.verb {
			case "GET":
				resp, cleanup = client.Get(route.path)
			case "POST":
				resp, cleanup = client.Post(route.path, nil)
			case "DELETE":
				resp, cleanup = client.Delete(route.path)
			case "PATCH":
				resp, cleanup = client.Patch(route.path, nil)
			case "PUT":
				resp, cleanup = client.Put(route.path, nil)
			default:
				t.Fatalf("Unknown HTTP verb %s\n", route.verb)
			}
			defer cleanup()

			assert.NotEqual(t, http.StatusUnauthorized, resp.StatusCode)
			assert.NotEqual(t, http.StatusForbidden, resp.StatusCode)
		}()
	}
}
```
