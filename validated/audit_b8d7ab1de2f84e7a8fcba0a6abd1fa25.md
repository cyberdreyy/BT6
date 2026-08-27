### Title
EI credential holders can run jobs by integer ID via `PipelineRunsController.Create` because `AuthenticateExternalInitiator` itself sets `SessionUserKey` - ([File: core/web/pipeline_runs_controller.go])

### Summary
The `isUser` gate in `PipelineRunsController.Create` is supposed to block external-initiator (EI) requests from triggering jobs by integer ID, but the check is based solely on whether `auth.GetAuthenticatedUser(c)` returns `ok == true`. The `AuthenticateExternalInitiator` middleware itself populates that same context key with a synthetic `User{Role: UserRoleRun}` object, so any EI-authenticated request satisfies `isUser` regardless of any session cookie being present. This is unrelated to a "stale cookie" — it happens purely from a valid EI credential.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` followed by `auth.RequiresRunRole(prc.Create)`: [1](#0-0) 

`auth.Authenticate` tries each `authMethod` in order and stops at the first one that does not return `auth.ErrorAuthFailed`: [2](#0-1) 

When a valid EI access key/secret pair is presented, `AuthenticateExternalInitiator` succeeds and explicitly sets both `SessionExternalInitiatorKey` and — critically — `SessionUserKey` to a synthetic user object with `Role: UserRoleRun`: [3](#0-2) 

This was done so that the generic `RequiresRunRole` wrapper (which calls `GetAuthenticatedUser`) treats EI credentials as satisfying the "run" role requirement: [4](#0-3) 

However, `PipelineRunsController.Create` reuses the exact same `GetAuthenticatedUser` helper to decide whether the caller is allowed to run a job by integer ID, explicitly commenting that EIs should be excluded: [5](#0-4) 

Since `AuthenticateExternalInitiator` sets `SessionUserKey` to a non-nil `*clsessions.User`, `auth.GetAuthenticatedUser(c)` returns `ok == true` for a pure EI-authenticated request — no session cookie, stale or otherwise, is required. The `isUser` check therefore does not distinguish EI credentials from genuine user sessions/API tokens, and the code proceeds to call `prc.App.RunJobV2(ctx, jobID, nil)` for the integer job ID supplied by the EI caller.

### Impact Explanation
An external-initiator credential — which is meant only to trigger jobs by UUID via the unauthenticated webhook/resume path and is a lower-trust credential than a full user session — can instead directly run any job referenced by its integer database ID. This is an authorization-bypass: it lets a lower-privileged credential holder perform an action (`RunJobV2`) explicitly intended to be restricted to genuine users, and it works against Chainlink's own comment/intent in the code. This matches the "role/authorization bypass — unauthorized job run" bounty impact class. It does not directly leak secrets or move funds, but it can trigger job pipeline execution (bridge calls, external adapter calls, etc.) on jobs the EI credential should not otherwise access.

### Likelihood Explanation
Precondition: attacker must hold a valid registered external-initiator credential (access key + secret) — no operator/admin access needed. Given the access-key/secret pair, exploitation is a single unauthenticated-role HTTP POST to `/v2/jobs/:ID/runs` with an integer job ID and the EI headers; no user session or stale cookie is required. This is fully deterministic and repeatable for any integer-ID job on the node.

### Recommendation
In `PipelineRunsController.Create`, do not rely on `auth.GetAuthenticatedUser` alone to gate integer-ID job runs. Explicitly check `auth.GetAuthenticatedExternalInitiator(c)` and reject the request if an EI principal is present, or introduce a dedicated context flag distinguishing "real user/token session" from "EI synthetic session" (e.g., a separate key set only by `AuthenticateBySession`/`AuthenticateByToken`, never by `AuthenticateExternalInitiator`), and gate int-ID runs on that flag instead.

### Proof of Concept
Handler-level integration test plan:
1. Set up a test app with an existing job identified by integer ID `jobID` and a registered `ExternalInitiator` (access key/secret) authorized with run capability.
2. Build a `POST /v2/jobs/{jobID}/runs` request:
   - Set headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`) to the valid EI credentials.
   - Do NOT attach any session cookie.
3. Execute the request against the router built by `NewRouter`.
4. Assert: response status is `200 OK` and a `pipelineRun` resource is returned (i.e., `RunJobV2` was invoked) — demonstrating the EI credential alone (no cookie) satisfies `isUser` and bypasses the intended "EIs not allowed for int ID" restriction.
5. As a control, repeat the same request with a stale/invalid session cookie added, and verify the outcome is identical (confirming the cookie is irrelevant — the bypass stems from `AuthenticateExternalInitiator` setting `SessionUserKey`, not from cookie confusion as originally hypothesized).
6. Add a unit test for `auth.GetAuthenticatedUser` directly after invoking `AuthenticateExternalInitiator` on a fresh `gin.Context`, asserting it returns `ok == true`, to pinpoint the root cause in `core/web/auth/auth.go`.

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
