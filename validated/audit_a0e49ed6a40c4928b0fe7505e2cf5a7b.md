### Title
EI credentials satisfy the `isUser` check and can trigger arbitrary job runs by integer job ID via `POST /v2/jobs/:ID/runs` - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value used for real users, assigning a synthetic `clsessions.User{Role: UserRoleRun}`. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` (i.e. presence of `SessionUserKey`) as its `isUser` gate intended to block EIs from running jobs by integer ID, but since EIs also populate `SessionUserKey`, `isUser` evaluates `true` for EI-authenticated requests too, defeating the intended EI/user distinction.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered under the `userOrEI` group with authentication methods tried in order: `AuthenticateExternalInitiator`, `AuthenticateByToken`, `AuthenticateBySession`, then wrapped by `auth.RequiresRunRole` [1](#0-0) .

`AuthenticateExternalInitiator` authenticates purely off `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers against the External Initiator's stored credentials, and on success sets both `SessionExternalInitiatorKey` and `SessionUserKey` to a synthetic `clsessions.User{Role: clsessions.UserRoleRun}` [2](#0-1) . `RequiresRunRole` only checks `user.Role != UserRoleView` [3](#0-2)  — the synthetic EI user passes since its role is `UserRoleRun`.

Inside `PipelineRunsController.Create`, the comment states "only users are allowed to run jobs using int IDs - EIs not allowed", and the check is:
```go
_, isUser := auth.GetAuthenticatedUser(c)
if isUser {
    ... jobID, _ := strconv.ParseInt(idStr, 10, 32)
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [4](#0-3) 

`GetAuthenticatedUser` simply checks whether `SessionUserKey` is set in the gin context [5](#0-4) . Because `AuthenticateExternalInitiator` sets `SessionUserKey` (not only `SessionExternalInitiatorKey`), `isUser` is `true` for EI-authenticated requests, and the code path intended only for "real" users (session/token auth) executes for EI credentials as well. This lets an EI credential holder run **any** job by integer ID via `prc.App.RunJobV2(ctx, jobID, nil)`, regardless of whether that job is associated with the EI (there is no ownership/association check between the EI and the job ID being run).

### Impact Explanation
An unprivileged EI-credential holder (a low-trust credential intended only to trigger jobs it is registered for, typically via webhook UUID flows that have since been removed per the `job.ErrJobTypeRemoved` check) can trigger arbitrary job runs on the node for any job by numeric ID, not just jobs associated with that EI. This is an authorization/role-confusion bypass: it enables unauthorized triggering of job execution (potentially consuming node resources, triggering external side effects such as bridge calls, VRF/OCR job pipeline runs, or other on-chain-adjacent actions depending on job type), corresponding to Chainlink's "unauthorized job run" impact class.

### Likelihood Explanation
The precondition is holding valid EI access key/secret for *any* registered external initiator on the node — no admin/session/API-token credentials are required. The bypass requires only a single crafted HTTP request (`POST /v2/jobs/:ID/runs` with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and an integer job ID). This is deterministic and repeatable for any int32 job ID; it does not depend on race conditions, timing, or any other weak preconditions.

### Recommendation
Distinguish EI-derived pseudo-users from real users at the check site. Options:
- Do not reuse `SessionUserKey` for EI authentication; instead have `PipelineRunsController.Create`'s `isUser` check explicitly ensure `GetAuthenticatedExternalInitiator(c)` is NOT set (i.e., `isUser := ok && !hasEI`), or
- Have `AuthenticateExternalInitiator` set a distinct context key (e.g., not `SessionUserKey`) so downstream consumers like `webauth.GetAuthenticatedUser` cannot be confused with genuine user sessions, and update `RequiresRunRole`/`isUser` logic accordingly to only accept genuine session/token-authenticated users for the int-ID run path.

### Proof of Concept
Go handler-level integration test in `core/web/pipeline_runs_controller_test.go` style:
1. Set up a test app with an `ExternalInitiator` registered with known access key/secret, and a separate `Job` (int32 ID) that is unrelated to that EI.
2. Issue `POST /v2/jobs/{jobID}/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to the EI's credentials (no session cookie, no `X-API-KEY`/`X-API-SECRET`).
3. Assert the response is `200 OK` with a `pipelineRun` resource returned (currently expected, demonstrating the bug), rather than the desired `422 Unprocessable Entity` "bad job ID" response that should occur for EI-authenticated requests using integer job IDs.
4. Unit-level assertion: call `auth.AuthenticateExternalInitiator` directly against a `gin.Context`, then call `auth.GetAuthenticatedUser(c)` and assert `isUser == true` (demonstrating the confusion) alongside `auth.GetAuthenticatedExternalInitiator(c)` also returning `true`, proving both are indistinguishable to `PipelineRunsController.Create`'s check.

### Citations

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

**File:** core/web/pipeline_runs_controller.go (L109-124)
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
```
