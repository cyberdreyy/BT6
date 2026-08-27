### Title
External-initiator credential can trigger arbitrary integer-ID job runs due to broken "EIs not allowed" check and missing EI-to-job binding - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` stores the EI's synthetic run-role user under the same context key (`SessionUserKey`) used for real session/token users. `PipelineRunsController.Create` tries to gate integer-ID job runs to "users only, EIs not allowed" by calling `auth.GetAuthenticatedUser(c)`, but that check only tests for the presence of `SessionUserKey`, which is also set for EI-authenticated requests. Combined with the fact that `RunJobV2` is invoked using only the URL `:ID` parameter with no verification that the authenticated `ExternalInitiator` is actually associated with that job, any valid EI AccessKey/Secret pair can trigger a run for any integer job ID, not just the job/webhook it was registered for.

### Finding Description
`AuthenticateExternalInitiator` authenticates the EI token and, regardless of which job/webhook spec the EI belongs to, sets a blanket run-role principal: [1](#0-0) 

`PipelineRunsController.Create` handles `POST /jobs/:ID/runs`. It reads the job ID from the URL, and gates integer-ID job runs with a check that is supposed to exclude EIs: [2](#0-1) 

The gate `_, isUser := auth.GetAuthenticatedUser(c)` calls: [3](#0-2) 

This only checks whether `SessionUserKey` exists in the gin context — it does not distinguish a real session/token user from an EI-authenticated request, because `AuthenticateExternalInitiator` sets that exact same key. As a result, `isUser` is `true` for EI-authenticated requests too, so the comment "only users are allowed to run jobs using int IDs — EIs not allowed" does not match the actual behavior implemented by the code. Once past that check, `prc.App.RunJobV2(ctx, jobID, nil)` is called using only the caller-supplied `jobID` — there is no lookup of `auth.GetAuthenticatedExternalInitiator(c)` nor any check that the identified `ExternalInitiator` is bound to that specific job. Any EI holding valid credentials for job/initiator A can therefore submit `POST /jobs/{B}/runs` with A's `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and trigger a run of job B.

### Impact Explanation
This breaks the intended one-EI-to-one-job/subscription binding invariant and allows cross-tenant unauthorized job run triggering: an external initiator that should only be permitted to invoke its own webhook can invoke pipeline runs for any job addressable by integer ID on the node, potentially causing unauthorized on-chain transactions or side effects if those jobs perform fund-moving or reporting actions. This matches the "unauthorized job run" impact class.

### Likelihood Explanation
The only precondition is possession of one valid, still-active EI AccessKey/Secret pair — a credential level explicitly assumed available to the attacker in this scenario. No admin/operator access is required; the request is a single unauthenticated-role HTTP POST with the EI headers set, and it is fully repeatable against any live job by iterating through integer job IDs.

### Recommendation
Fix `PipelineRunsController.Create`'s guard to actually distinguish user-vs-EI callers by checking `auth.GetAuthenticatedExternalInitiator(c)` (reject if present) instead of relying on `GetAuthenticatedUser`, since `SessionUserKey` is shared between both. More robustly, give EI-authenticated principals a distinct context marker (not `clsessions.User`), and additionally verify — for any EI-driven run path that remains — that the authenticated `ExternalInitiator` is actually associated with the target job/webhook spec before calling `RunJobV2`.

### Proof of Concept
1. Seed two `ExternalInitiator` DB rows (A, B) each with distinct AccessKey/Secret, and two jobs (JobA int ID, JobB int ID) with no relation to either EI (since webhook job type is removed, use any int-ID job type reachable by `RunJobV2`).
2. Build a `gin` test router wiring `POST /v2/jobs/:ID/runs` to `PipelineRunsController.Create` behind `auth.Authenticate(store, auth.AuthenticateBySession, auth.AuthenticateExternalInitiator)` as done in `router.go`.
3. Send `POST /v2/jobs/{JobB.ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to initiator A's credentials.
4. Assert current behavior: request succeeds (200, pipelineRun resource for JobB returned) even though initiator A has no relation to JobB — expected/secure behavior should be `403`/`404`.
5. Confirm root cause via unit test on `auth.GetAuthenticatedUser` showing it returns `ok=true` after `AuthenticateExternalInitiator` runs, and that `PipelineRunsController.Create`'s `isUser` branch is entered for EI-authenticated contexts.

### Citations

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
