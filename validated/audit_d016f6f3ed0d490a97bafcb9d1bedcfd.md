### Title
External Initiator can run any job by numeric ID via PipelineRunsController.Create, bypassing per-job EI binding - (File: core/web/pipeline_runs_controller.go)

### Summary
The `POST /v2/jobs/:ID/runs` route is served by `PipelineRunsController.Create`, and is reachable both by session/token-authenticated Users and by External-Initiator (EI) credentials via `auth.AuthenticateExternalInitiator`. Because `AuthenticateExternalInitiator` injects a synthetic `&clsessions.User{Role: UserRoleRun}` into the same `SessionUserKey` context slot used for real users, `auth.GetAuthenticatedUser(c)` returns `ok == true` for EI callers, causing `Create` to treat them as a "user" and call `prc.App.RunJobV2(ctx, jobID, nil)` for an arbitrary numeric job ID with no check that the job is the one the EI is bound to.

### Finding Description
Route wiring in `core/web/router.go`: [1](#0-0) 
shows `/v2/jobs/:ID/runs` is guarded only by `auth.RequiresRunRole(prc.Create)`, with the authentication chain `AuthenticateExternalInitiator, AuthenticateByToken, AuthenticateBySession`.

`AuthenticateExternalInitiator` sets the exact same context key (`SessionUserKey`) as real user auth, assigning a run-role `User`: [2](#0-1) 

`PipelineRunsController.Create` then does: [3](#0-2) 
`isUser` is `true` for both real session/token users and EI callers, since `GetAuthenticatedUser` just checks presence/type of the `SessionUserKey` value: [4](#0-3) 
The code comment "only users are allowed to run jobs using int IDs - EIs not allowed" is factually incorrect given how `AuthenticateExternalInitiator` populates the same key — there is no separate check (e.g., verifying `GetAuthenticatedExternalInitiator(c)` is unset, or verifying the EI is bound to `jobID`) before calling `RunJobV2` with an attacker-supplied job ID. `RunJobV2` itself takes only `ctx` and `jobID` with no EI/job-binding parameter, so no authorization check happens downstream either.

### Impact Explanation
An external-initiator credential holder (a low-privilege, narrowly-scoped credential meant to trigger runs only for the webhook job it is bound to) can trigger execution of **any** job on the node by numeric ID, including jobs it has no relationship to. This is an authorization/binding bypass allowing unauthorized triggering of arbitrary job runs (e.g., OCR/VRF/keeper/other jobs), which can cause unintended on-chain transactions, fund movement, or unauthorized use of node resources/keys tied to unrelated jobs — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Preconditions are minimal: possession of any valid External Initiator access key/secret pair (the lowest-privilege credential type intended only to bind to one job) is sufficient. No admin/edit role or session is required. The exploit is a single HTTP POST with attacker-chosen path parameter, trivially repeatable.

### Recommendation
In `PipelineRunsController.Create`, distinguish EI-authenticated requests from real user sessions/tokens (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` and, if present, validate that the target job's `ExternalJobID`/EI binding matches the authenticated EI before calling `RunJobV2`), rather than relying solely on the shared `SessionUserKey`/role check. Alternatively, use a distinct context key or wrapper type for EI-derived pseudo-users so `GetAuthenticatedUser` cannot conflate them with genuine users, and enforce the job-EI binding check server-side prior to invoking `RunJobV2`.

### Proof of Concept
Go handler-level integration test plan:
1. Create job A and job B (e.g., via `cltest` job fixtures) and an External Initiator record bound only to job A (using `ExternalInitiator`/webhook spec linking to job A).
2. Build the router via `web.NewRouter(app, ...)` with the EI persisted in the DB.
3. Send `POST /v2/jobs/<jobB_numeric_id>/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`) set to the EI's credentials for job A.
4. Assert the response is an error (expected: 401/403/422) and that `prc.App.RunJobV2` was never invoked for job B — i.e., no `pipeline_runs` row created for job B.
5. Repeat with `POST /v2/jobs/<jobA_numeric_id>/runs` to confirm the EI's legitimate job A run still succeeds, establishing that the fix must reject job B specifically while preserving job A.

Note: I could not fully inspect `core/services/chainlink/application.go`'s `RunJobV2` implementation body within tool limits, but the two matches found were consistent with only the interface declaration and delegation to the pipeline runner without an EI/job binding parameter, so no downstream authorization check appears to exist.

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
