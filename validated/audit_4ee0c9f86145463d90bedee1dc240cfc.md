### Title
External Initiator credentials bypass the "EIs not allowed" restriction and can trigger runs of *any* job by ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` intends to restrict direct job-run-by-ID triggering to real dashboard/API users, explicitly commenting "only users are allowed to run jobs using int IDs - EIs not allowed." However, the `isUser` check it relies on (`auth.GetAuthenticatedUser`) also succeeds for requests authenticated via `auth.AuthenticateExternalInitiator`, because that authenticator injects a synthetic `*clsessions.User{Role: UserRoleRun}` into the same context key used for real users. As a result, any holder of valid External Initiator (EI) access-key/secret credentials can call `POST /v2/jobs/:ID/runs` for an arbitrary job ID, not just the job tied to their EI.

### Finding Description
The route is registered in `core/web/router.go`: [1](#0-0) 

`userOrEI` accepts EI, token, or session auth, then wraps the handler with `auth.RequiresRunRole`, which only checks that the (real or synthetic) user's role is not `View`: [2](#0-1) 

The actual "EIs not allowed" enforcement is supposed to happen inside the handler: [3](#0-2) 

But `auth.AuthenticateExternalInitiator` sets the exact same `SessionUserKey` context value used by real user auth, giving the EI a fake `User{Role: UserRoleRun}`: [4](#0-3) 

Because `auth.GetAuthenticatedUser` simply reads `SessionUserKey` and type-asserts to `*clsessions.User`: [5](#0-4) 

`isUser` in `Create` is `true` for EI-authenticated requests as well as genuine users, so the intended EI exclusion never triggers, and `prc.App.RunJobV2(ctx, jobID, nil)` runs unconditionally for any numeric job ID supplied in the URL, regardless of which job (if any) the calling EI is actually associated with.

### Impact Explanation
Any attacker holding valid EI credentials (a narrowly-scoped credential intended only to be used by the specific bridge/initiator wired to one job) can invoke `RunJobV2` for any job on the node by iterating job IDs, which triggers unauthorized execution of pipelines belonging to other jobs/users. Depending on job configuration this can cause on-chain transactions, VRF fulfillments, or other side effects tied to job execution — an unauthorized job run within the "unauthorized job run or fund movement" bounty impact class.

### Likelihood Explanation
Exploitation requires only a valid EI access key/secret pair (the lowest-privilege, non-admin credential explicitly permitted by this route) and knowledge/guessing of a target integer job ID, which is enumerable since job IDs are sequential integers. No operator or admin access is required, and the request is trivially repeatable via a single authenticated HTTP POST.

### Recommendation
In `PipelineRunsController.Create`, distinguish EI-authenticated requests from real user-authenticated requests explicitly (e.g., via `auth.GetAuthenticatedExternalInitiator(c)`) rather than relying on the overloaded `SessionUserKey`, and reject EI-authenticated requests for arbitrary job IDs, or verify that the EI is authorized for the specific job ID being triggered before calling `RunJobV2`.

### Proof of Concept
Handler-level integration test plan (Go, using `httptest` + gin, mirroring existing tests in `core/web/pipeline_runs_controller_test.go`):
1. Set up a test app with two jobs: `jobA` (int ID, no associated EI) and register one External Initiator `ei1` (not tied to `jobA`).
2. Build a request `POST /v2/jobs/<jobA.ID>/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `ei1`'s credentials (no session/API token headers).
3. Send through the router built by `NewRouter`.
4. Assert response status is `200 OK` and a `pipelineRun` resource is returned for `jobA`, proving the EI (unrelated to `jobA`) successfully triggered its run — expected/desired behavior would be `422`/`401` per the "EIs not allowed" comment.
5. Add a unit test for `auth.GetAuthenticatedUser` demonstrating it returns `ok=true` after `AuthenticateExternalInitiator` runs, confirming the root cause.

### Citations

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
}
```

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/auth/auth.go (L178-187)
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
}
```

**File:** core/web/auth/auth.go (L202-217)
```go
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
