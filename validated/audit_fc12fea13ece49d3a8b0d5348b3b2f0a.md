### Title
External Initiator credential can bypass "EI not allowed" restriction and trigger arbitrary job runs by numeric job ID - ([File: core/web/router.go], [File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
The `userOrEI` route group at `core/web/router.go` accepts requests authenticated via `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, or `auth.AuthenticateBySession` for `POST /v2/jobs/:ID/runs`, guarded by `auth.RequiresRunRole`. `PipelineRunsController.Create` attempts to restrict numeric-ID job runs to real users only ("EIs not allowed"), but the check it relies on (`auth.GetAuthenticatedUser`) is satisfied by external initiators too, because the EI auth path injects a synthetic `User` object into the same session key.

### Finding Description
`userOrEI` is defined as: [1](#0-0) 

The handler `PipelineRunsController.Create` gates numeric-ID job execution on being a "user" (as opposed to an external initiator), stating in a comment that "only users are allowed to run jobs using int IDs - EIs not allowed": [2](#0-1) 

That check is `_, isUser := auth.GetAuthenticatedUser(c)`. However, `auth.AuthenticateExternalInitiator` — the very method used to authenticate external initiators for this route — also sets the `SessionUserKey` to a synthetic `User{Role: clsessions.UserRoleRun}`: [3](#0-2) 

`GetAuthenticatedUser` simply checks for the presence of that key and does not distinguish a real, DB-backed user from this synthetic run-role stand-in created for EI requests: [4](#0-3) 

Consequently, an External Initiator credential holder authenticating via `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers passes both `auth.RequiresRunRole` (role is `UserRoleRun`, which is not `UserRoleView`) and the `isUser` check in `Create`, allowing it to submit an integer job ID and call `prc.App.RunJobV2(ctx, jobID, nil)` for **any** job in the node, not just a job specifically associated with that external initiator. The webhook-UUID path that would have scoped an EI to its own job was explicitly removed ("Webhook runs used external job UUIDs; that job type has been removed"), but the integer-ID path meant to be blocked for EIs is not actually blocked due to the shared session key / synthetic-user design.

### Impact Explanation
This is an authorization bypass allowing an attacker holding only an external-initiator credential (not an admin/edit/run user account) to trigger job runs for arbitrary jobs on the node by numeric ID, including jobs they have no legitimate association with. Depending on the job types configured on the node (e.g., jobs that submit on-chain transactions, VRF fulfillment, keeper/automation jobs, or flux monitor jobs), this can result in unauthorized job execution and potentially unauthorized fund-moving transactions being initiated — matching the "unauthorized job run or fund movement" bounty impact class.

### Likelihood Explanation
The only precondition is possession of valid external-initiator access key/secret (the minimum unprivileged credential type explicitly listed as an allowed attacker profile in the rules). No admin, edit, or session/API-token user role is required. The exploit is a single, repeatable HTTP POST to `/v2/jobs/:ID/runs` with the target job's numeric ID; no race conditions or timing dependencies are involved.

### Recommendation
In `PipelineRunsController.Create`, distinguish a real authenticated user from the synthetic EI stand-in — e.g., check `auth.GetAuthenticatedExternalInitiator(c)` first and reject numeric-ID runs if an external initiator is present, rather than relying solely on `GetAuthenticatedUser`. Alternatively, avoid setting `SessionUserKey` for EI-authenticated requests, or use a distinct context key/type that cannot be conflated with real users in role/identity checks throughout the codebase.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go`):
1. Set up an app with a job created for a normal user, and separately register a valid `ExternalInitiator` (different from that job) via `bridges.ExternalInitiator`.
2. Issue `POST /v2/jobs/:ID/runs` (where `:ID` is the numeric ID of the job, unrelated to the EI) using headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` for the registered external initiator, and no session cookie or user API token.
3. Assert the response is `200 OK` with a `pipelineRun` resource returned (i.e., `RunJobV2` was invoked), demonstrating the EI successfully triggered a run for a job it has no association with.
4. Assert this contradicts the expected behavior implied by the code comment ("EIs not allowed" for int IDs), which should instead yield `422 Unprocessable Entity` ("bad job ID") for EI-authenticated requests using numeric IDs.

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

**File:** core/web/auth/auth.go (L119-151)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

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
