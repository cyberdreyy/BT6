### Title
External Initiator credentials can trigger runs on arbitrary jobs by int ID, bypassing the "EIs not allowed" check in `PipelineRunsController.Create` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value used for regular user sessions (a synthetic `clsessions.User{Role: clsessions.UserRoleRun}`), so any code that calls `auth.GetAuthenticatedUser(c)` cannot distinguish a genuine logged-in user from an authenticated External Initiator (EI). `PipelineRunsController.Create` explicitly relies on this distinction (`isUser` check, with a comment stating "only users are allowed to run jobs using int IDs - EIs not allowed") to gate integer-job-ID run triggering, but because the check is satisfied for EI callers too, an EI credential for job A can call `POST /v2/jobs/<jobB-ID>/runs` and trigger `RunJobV2` for any job by numeric ID, with no per-job/EI binding check.

### Finding Description
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` authenticates the EI's access key/secret against the specific `bridges.ExternalInitiator` record via `store.FindExternalInitiator` and `bridges.AuthenticateExternalInitiator`, but afterward it unconditionally does: [1](#0-0) 
This sets `SessionUserKey` to a freshly-constructed `User{Role: UserRoleRun}` — the very same context key that `AuthenticateBySession`/`AuthenticateByToken` use for real, authenticated human users: [2](#0-1) 

`GetAuthenticatedUser` simply reads that key without differentiating origin: [3](#0-2) 

`PipelineRunsController.Create` (handling `POST /v2/jobs/:ID/runs`) uses exactly this ambiguous check to decide whether integer job IDs may be run, with an explicit comment that EIs are not supposed to be allowed: [4](#0-3) 

Because `isUser` is `true` for both real users and EI-authenticated callers, an EI-authenticated request supplying an int32 job ID bypasses the intended "EIs not allowed" restriction and reaches `prc.App.RunJobV2(ctx, jobID, nil)` directly. There is no check anywhere in this path that the `jobID` parameter corresponds to the job/webhook the EI credential was issued for — the EI's own job association (looked up in `FindExternalInitiator`) is never cross-checked against the `:ID` path parameter. `RequiresRunRole` only checks `user.Role != UserRoleView`, which the synthetic EI user satisfies, so the middleware layer provides no additional protection.

### Impact Explanation
An External Initiator credential scoped to one job/webhook (job A) can trigger pipeline runs on any other job in the node by numeric ID (job B), as long as job B's ID is guessable or known (sequential integer IDs make this trivial). This is unauthorized job-run triggering across job/tenant boundaries — matching the "unauthorized job run" bounty impact class. Depending on what job B does (e.g., initiating on-chain transactions, VRF fulfillment, other side effects), this could cause unintended fund movement or resource exhaustion using a credential never authorized for that job.

### Likelihood Explanation
Only a valid EI `AccessKey`/`Secret` pair for any single job is required — this is a "restricted credential holder" attacker per the threat model, not an operator/admin. The job ID path parameter is a simple integer with no per-EI ownership check, so exploitation is a single crafted HTTP request. No timing, race, or infrastructure requirements — fully repeatable.

### Recommendation
Do not reuse `SessionUserKey`/`GetAuthenticatedUser` to represent External Initiators. Introduce a distinct context key/type (e.g., `SessionExternalInitiatorKey` already exists — use `GetAuthenticatedExternalInitiator` exclusively for EI-authorization checks instead of a synthetic `User`), and have `PipelineRunsController.Create` check `auth.GetAuthenticatedExternalInitiator(c)` to positively identify EI callers (rather than only checking for the absence of `isUser`), then enforce that the target job (`:ID`) is the specific job/webhook associated with that EI record (e.g., compare against `ei.WebhookSpecID`/associated job) before invoking `RunJobV2`.

### Proof of Concept
1. Create two jobs, A and B, each with a distinct `bridges.ExternalInitiator` (or webhook spec) such that only EI-A is authorized for job A.
2. Authenticate as EI-A (valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers for EI-A).
3. Issue `POST /v2/jobs/<jobB.ID>/runs` (job B's numeric ID) using EI-A's credentials.
4. Assert: `PipelineRunsController.Create` returns success (200/201) and `prc.App.RunJobV2` is invoked with job B's ID, even though the credential belongs only to EI-A/job A — i.e., write a handler-level test in `core/web/pipeline_runs_controller_test.go` style that sets up the router with `Authenticate(..., AuthenticateExternalInitiator)` middleware and confirms `isUser`/`RunJobV2` is reached for an EI-authenticated request with an integer job ID that is not the EI's own job, demonstrating the missing per-job binding check.

### Citations

**File:** core/web/auth/auth.go (L63-71)
```go
	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
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

**File:** core/web/pipeline_runs_controller.go (L101-125)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}

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
