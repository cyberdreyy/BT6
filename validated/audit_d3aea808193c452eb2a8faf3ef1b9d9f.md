### Title
External Initiator credentials bypass the "EIs not allowed" integer-ID job-run restriction via synthetic user impersonation - (File: core/web/pipeline_runs_controller.go)

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value that real session/token authentication uses, storing a synthetic `clsessions.User{Role: clsessions.UserRoleRun}`. `PipelineRunsController.Create` only calls `auth.GetAuthenticatedUser(c)` to decide whether the caller is a "user" allowed to trigger jobs by integer ID, so it cannot distinguish this EI-derived pseudo-user from a genuine authenticated user, directly contradicting its own comment that "EIs not allowed."

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered with a multi-method middleware chain that tries EI, token, then session authentication in that order: [1](#0-0) 

When an attacker authenticates purely as an external initiator (`X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers), `AuthenticateExternalInitiator` succeeds and explicitly writes a synthetic user into the same context key used for real users: [2](#0-1) 

`RequiresRunRole` (wrapping `prc.Create`) only checks `user.Role != UserRoleView`, which the synthetic `UserRoleRun` satisfies, so it passes through: [3](#0-2) 

Inside `Create`, the check meant to gate integer-ID job runs to "real" users is simply: [4](#0-3) 

`GetAuthenticatedUser` only type-asserts whatever object is stored under `SessionUserKey`: [5](#0-4) 

Since the EI authenticator stores a `*clsessions.User` there too, `isUser` evaluates to `true` for EI-authenticated requests, and the integer-ID branch (`prc.App.RunJobV2(ctx, jobID, nil)`) executes — exactly the behavior the inline comment says should be blocked for EIs.

### Impact Explanation
An external-initiator credential holder (a comparatively low-trust bridge/data-source credential, not a node operator or full API user) can trigger arbitrary jobs by numeric ID that were not designed to be EI-triggerable, bypassing the documented access-control invariant. This is an authorization bypass / unauthorized action performed under an ambiguous identity (the audit trail and any per-EI restrictions on which jobs an EI may run are circumvented, since `RunJobV2` here takes no EI-specific scoping), matching Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
The precondition is minimal: possession of a single valid EI access key/secret pair (which are lower-trust, narrowly-scoped credentials intended only to feed webhook data), no user session, password, or API token required. The exploit is a single HTTP POST to a documented, always-mounted route (`userOrEI.POST("/jobs/:ID/runs", ...)`), fully deterministic and repeatable.

### Recommendation
In `PipelineRunsController.Create`, do not rely solely on `GetAuthenticatedUser`'s type assertion to distinguish real users from EIs. Either check `auth.GetAuthenticatedExternalInitiator(c)` first and reject if present, or mark the synthetic EI user distinctly (e.g., a dedicated context key or a sentinel field) so `isUser` cannot be satisfied by the EI-derived pseudo-user.

### Proof of Concept
Go handler-level test:
1. Build a gin engine/context with an `ExternalInitiator` fixture and `bridges.ExternalInitiator` auth data.
2. Call `auth.AuthenticateExternalInitiator(c, mockAuthenticator)` directly (bypassing session/token auth), which sets `SessionUserKey` to `&clsessions.User{Role: UserRoleRun}`.
3. Set `c.Params` with an integer `ID` (e.g., `"1"`), call `PipelineRunsController.Create(c)`.
4. Assert that `App.RunJobV2` mock IS invoked (demonstrating the bypass) — currently succeeds — whereas the intended behavior (per the code comment) should return `422 Unprocessable Entity` / "bad job ID" and `RunJobV2` should NOT be called.

### Citations

**File:** core/web/router.go (L449-456)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

**File:** core/web/auth/auth.go (L143-151)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
```

**File:** core/web/auth/auth.go (L177-187)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
}
```

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/pipeline_runs_controller.go (L109-125)
```go
	_, isUser := auth.GetAuthenticatedUser(c)
	// only users are allowed to run jobs using int IDs - EIs not allowed
	if isUser {
		// Is it an int32? Then process it regardless of type
		var jobID int32
		jobID64, err := strconv.ParseInt(idStr, 10, 32)
		if err == nil {
			jobID = int32(jobID64)
			jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
			if err != nil {
				jsonAPIError(c, http.StatusInternalServerError, err)
				return
			}
			respondWithPipelineRun(jobRunID)
			return
		}
	}
```
