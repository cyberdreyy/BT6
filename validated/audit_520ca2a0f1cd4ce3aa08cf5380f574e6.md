No vulnerability found for this question.

**Analysis:** In `core/web/router.go`, `AuthenticateExternalInitiator` is only wired into the middleware chain of the `userOrEI` route group [1](#0-0) , which registers exactly `GET /v2/ping` and `POST /v2/jobs/:ID/runs` (the latter wrapped in `RequiresRunRole`). The routes cited as at-risk—`POST /v2/replay_from_block/:number`, `GET /v2/find_lca`, `POST /v2/lp_skip_to_block`, and (in dev builds) `POST /v2/execute_capability`—are registered on the separate `authv2` group, whose middleware chain only includes `auth.AuthenticateByToken` and `auth.AuthenticateBySession` [2](#0-1) [3](#0-2) .

Because gin route groups bind a fixed middleware chain to the specific routes registered on that group instance, a request to `/v2/replay_from_block/...` never executes `AuthenticateExternalInitiator`; an EI credential holder presenting only `static.ExternalInitiatorAccessKeyHeader`/`Secret` headers (with no session cookie or `X-API-KEY`/`X-API-SECRET`) will fail both `AuthenticateByToken` and `AuthenticateBySession` and receive a 401 before ever reaching `RequiresRunRole` [4](#0-3) . The unconditional `Role: UserRoleRun` assignment in `AuthenticateExternalInitiator` [5](#0-4)  is therefore only ever consulted by handlers reachable through the `userOrEI` group, which is deliberately scoped to job-triggering endpoints (`/ping`, `/jobs/:ID/runs`). This confines the elevated role to its intended surface, so the described escalation path to `ReplayFromBlock`/`FindLCA`/`LPSkipToBlock` is not reachable via normal HTTP routing.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L297-302)
```go
		rc := ReplayController{app}
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
		lcaC := LCAController{app}
		authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))
		lpSkipC := LPSkipController{app}
		authv2.POST("/lp_skip_to_block", auth.RequiresRunRole(lpSkipC.LPSkipToBlock))
```

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

**File:** core/web/auth/auth.go (L145-150)
```go
	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/auth/auth.go (L157-175)
```go
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
}
```
