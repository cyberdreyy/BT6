### Title
External Initiator credentials bypass the "EIs not allowed" check in `PipelineRunsController.Create` because `AuthenticateExternalInitiator` also sets `SessionUserKey` - ([File: core/web/auth/auth.go])

### Summary
The `POST /v2/jobs/:ID/runs` route is protected by `auth.Authenticate` with `AuthenticateExternalInitiator` as one of its allowed methods. That method sets both the external-initiator context key and a synthetic `*sessions.User{Role: UserRoleRun}` under `SessionUserKey`. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the caller is a "real" user (in which case numeric job IDs are allowed to run), explicitly to keep EIs from running jobs by integer ID. Because EI auth populates the same `SessionUserKey`, `isUser` is `true` for EI-only requests, defeating the intended restriction.

### Finding Description
The route is registered in `core/web/router.go`:
```go
userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
    auth.AuthenticateExternalInitiator,
    auth.AuthenticateByToken,
    auth.AuthenticateBySession,
))
userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
``` [1](#0-0) 

`AuthenticateExternalInitiator` in `core/web/auth/auth.go` authenticates the EI access key/secret, then sets *both* the EI key and a synthetic run-role user:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [2](#0-1) 

`GetAuthenticatedUser` simply reads `SessionUserKey` without distinguishing whether it was populated by a real user-authentication path or by the EI shim:
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	...
	user, ok := obj.(*clsessions.User)
	return user, ok
}
``` [3](#0-2) 

`PipelineRunsController.Create` relies on this to gate integer-ID job runs to real users:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    var jobID int32
    jobID64, err := strconv.ParseInt(idStr, 10, 32)
    if err == nil {
        jobID = int32(jobID64)
        jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
        ...
        respondWithPipelineRun(jobRunID)
        return
    }
}
jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
``` [4](#0-3) 

Because `isUser` is always `true` after successful EI authentication (it's the same `*sessions.User` type check regardless of which `authMethod` populated the key), an attacker holding only an External Initiator access key/secret can send `POST /v2/jobs/<int-ID>/runs` with the `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers, pass `RequiresRunRole` (role is `UserRoleRun`), and reach `RunJobV2` for any numeric job ID on the node — exactly the behavior the code comment says is disallowed.

### Impact Explanation
This is an authorization-bypass / unauthorized job run: a low-trust, job-scoped External Initiator credential (intended only to trigger the specific webhook job it's bound to) can be used to trigger the pipeline run of *any* job on the node by numeric ID, not just the job it is registered for. Depending on job types configured on the node (e.g., jobs that move funds, write on-chain transactions, or call external bridges), this can result in unauthorized transaction execution or unintended job invocation, matching Chainlink's "unauthorized job run" bounty class.

### Likelihood Explanation
The only precondition is possession of a valid External Initiator access key/secret — the lowest-privilege credential class supported by the router (`userOrEI` group). No user session, API token, or edit/admin role is required. The exploit is deterministic and repeatable: any request to `POST /v2/jobs/:intID/runs` with valid EI headers reaches `RunJobV2` regardless of which job the EI was created for, as long as the target ID parses as an `int32`.

### Recommendation
Do not overload `SessionUserKey` for EI authentication. Introduce a distinct check (e.g., `auth.GetAuthenticatedExternalInitiator(c)` presence, or a dedicated "authMethod" marker in context) so `PipelineRunsController.Create` can positively confirm the caller is a real user session/token, not merely that `SessionUserKey` is set. For example, check `_, isEI := auth.GetAuthenticatedExternalInitiator(c); if isEI { reject }` before allowing numeric-ID runs, or set a separate context flag (`SessionAuthMethodKey`) distinguishing session/token auth from EI auth.

### Proof of Concept
Handler-level integration test plan:
1. Start a test app (`cltest.NewApplicationEVMDisabled`), create a job via `job.ORM` with a known int32 ID.
2. Create an External Initiator via the ORM (`bridges.ExternalInitiator`) with a known access key/secret, unrelated to the created job.
3. Issue `POST /v2/jobs/<jobID>/runs` using an HTTP client that sets only the EI headers (`X-Chainlink-EA-AccessKey`, `X-Chainlink-EA-Secret`) — no session cookie, no API token.
4. Assert current (vulnerable) behavior: response status `200`/`201` with a `pipelineRun` resource returned (job triggered), i.e., `isUser` was `true` and `RunJobV2` was invoked.
5. Add a unit test on `auth.GetAuthenticatedUser` directly: build a `gin.Context`, run `auth.AuthenticateExternalInitiator(c, mockStore)`, then call `auth.GetAuthenticatedUser(c)` and assert it returns `ok=false` (expected fix) instead of `ok=true` (current bug).
6. After the fix, re-run step 3/4 and assert `http.StatusUnprocessableEntity` with body containing `"bad job ID"`, confirming EIs can no longer trigger runs by integer ID.

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

**File:** core/web/pipeline_runs_controller.go (L109-127)
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

	jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
```
