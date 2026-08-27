### Title
EI-authenticated request can trigger a run on any job by int ID, bypassing the "EIs not allowed" job-type restriction - ([File: core/web/pipeline_runs_controller.go], [File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value that `AuthenticateBySession`/`AuthenticateByToken` set for a normal user, giving the EI-authenticated request a synthetic `User{Role: UserRoleRun}`. `PipelineRunsController.Create` decides whether to allow triggering a run purely by checking `auth.GetAuthenticatedUser(c)` (`isUser`), which returns `true` for this synthetic user even though the caller is really an External Initiator, not a user. As a result an EI credential can invoke `RunJobV2` on any arbitrary int32 job ID regardless of whether that job's spec type is `external`/webhook-bound to that EI.

### Finding Description
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` authenticates the EI token/secret, then does: [1](#0-0) 
This sets `SessionExternalInitiatorKey` to the EI object but *also* sets `SessionUserKey` to a fabricated `User{Role: UserRoleRun}`.

`PipelineRunsController.Create` in `core/web/pipeline_runs_controller.go` determines eligibility to run a job with an int32 ID using only: [2](#0-1) 
The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but the check `_, isUser := auth.GetAuthenticatedUser(c)` reads `SessionUserKey`, which is populated identically for a genuine user session/token *and* for an EI-authenticated request (per the code above). There is no check that distinguishes an EI-derived pseudo-user from a real user, and no check that the target `jobID` corresponds to a job spec whose initiator/type is bound to the authenticated EI (`GetAuthenticatedExternalInitiator`). The handler simply calls `prc.App.RunJobV2(ctx, jobID, nil)` with the attacker-supplied `jobID`, with no ownership/type validation against the EI's identity.

Consequently, any holder of a valid EI credential can POST to the runs-creation route with an arbitrary integer job ID (e.g., a cron job, keeper job, or another EI's job) and trigger a pipeline run on it, contradicting the intended invariant that "requests are bound to exactly one authorized job."

### Impact Explanation
This is an authorization-bypass vulnerability (broken object-level authorization / IDOR): an attacker holding only a low-privilege EI credential can trigger unauthorized job runs on jobs unrelated to their EI, causing unintended external effects (e.g., firing keeper/cron jobs, unauthorized bridge calls, external HTTP task execution) and potential resource exhaustion, matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs one valid EI access key/secret (the lowest-privilege non-user credential type). The exploit requires no further recon beyond guessing/knowing a numeric job ID (job IDs are small sequential integers, easily enumerable). The request is a single HTTP POST, fully repeatable.

### Recommendation
In `PipelineRunsController.Create`, explicitly distinguish EI-authenticated requests from real user sessions (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` first and reject/branch separately), and when handling an EI-triggered run, verify that the target job's spec (`job.Job.ExternalInitiatorWebhookSpec` or equivalent) is actually bound to the authenticated EI's ID/name before calling `RunJobV2`. Do not rely solely on `SessionUserKey` presence, since `AuthenticateExternalInitiator` populates it with a synthetic user for role-check convenience elsewhere.

### Proof of Concept
1. Create two jobs: Job A (cron/keeper type, unrelated to any EI) and Job B associated with EI `E1`.
2. Register EI `E1` with valid access key/secret via `FindExternalInitiator`.
3. Send `POST /v2/runs/{JobA.ID}` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to `E1`'s credentials (using `AuthenticateExternalInitiator` middleware chain).
4. Assert current behavior: handler treats request as `isUser == true` (because `SessionUserKey` was set), calls `RunJobV2(ctx, JobA.ID, nil)`, and responds `200 OK` with a created pipeline run — even though `E1` has no relationship with Job A.
5. Expected (fixed) behavior: handler should return `403 Forbidden` (or `404`) because `E1` is not authorized for Job A, and no run should be created — add integration test in `core/web/pipeline_runs_controller_test.go` asserting the run is rejected when `job.ExternalInitiator != authenticated EI`.

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
