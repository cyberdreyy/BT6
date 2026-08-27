### Title
EI-authenticated requests satisfy the `isUser` check in `PipelineRunsController.Create`, bypassing the "EIs not allowed" restriction on numeric job-ID runs - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` intends to restrict triggering a job run by numeric (`int32`) job ID to authenticated dashboard/API-token users, explicitly excluding External Initiators (EIs), per the comment `// only users are allowed to run jobs using int IDs - EIs not allowed`. However, the check `_, isUser := auth.GetAuthenticatedUser(c)` is satisfied for EI-authenticated requests as well, because `AuthenticateExternalInitiator` also populates the same `SessionUserKey` context value with a synthetic `clsessions.User{Role: clsessions.UserRoleRun}`.

### Finding Description
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` sets both `SessionExternalInitiatorKey` and `SessionUserKey`: [1](#0-0) 

`GetAuthenticatedUser` simply reads `SessionUserKey` from context and returns `ok=true` if any `*clsessions.User` is present, with no distinction between a real session/token-derived user and the synthetic EI user: [2](#0-1) 

In `PipelineRunsController.Create`, the guard comment explicitly states the intent that EIs should not be able to trigger runs via numeric job IDs, but the implementation checks only `isUser` from `GetAuthenticatedUser`, which is true for both real users and EI-authenticated requests: [3](#0-2) 

Because the earlier UUID branch now unconditionally rejects webhook-style UUID job IDs (since the webhook job type was removed), and EI credentials satisfy `isUser`, an EI credential holder can supply an arbitrary numeric job ID and call `prc.App.RunJobV2(ctx, jobID, nil)` for any job in the system — there is no check that ties the numeric job ID to the specific job(s) the External Initiator is authorized for. This is a logic mismatch between the code comment's stated authorization intent and the actual context-key-based check, which allows a code path documented as "EIs not allowed" to be reached by EI credentials.

### Impact Explanation
This allows a caller possessing only External Initiator credentials (a lower-trust identity intended only to trigger its own webhook-linked job) to trigger `RunJobV2` for an arbitrary job ID belonging to any job in the node, not limited to jobs the EI is associated with. This matches the "unauthorized job run" bounty impact class, since it can trigger execution of pipelines/jobs (potentially including ones that move funds or perform privileged on-chain actions) that the EI credential was never provisioned to invoke.

### Likelihood Explanation
Exploitation requires only a valid External Initiator access key/secret pair (a low-privilege credential type, by design meant to only trigger a specific webhook job) and reachability of the route bound to `PipelineRunsController.Create` under the middleware stack that includes `auth.AuthenticateExternalInitiator`. Given the router wiring for the pipeline-runs "create" route was not fully confirmed in this pass (I could not conclusively verify from the router.go excerpt read which combination of `AuthenticateBySession`/`AuthenticateByToken`/`AuthenticateExternalInitiator` methods guard the `POST /v2/jobs/:ID/runs` route), likelihood is contingent on that route including EI authentication. Historically in this codebase the run-trigger route accepts EI auth precisely to allow webhook-style triggering, which is consistent with the comment in `Create` acknowledging EIs as a possible caller type.

### Recommendation
Distinguish EI-derived pseudo-users from real authenticated users, e.g., by not overloading `SessionUserKey` for EI requests, or by adding an explicit `SessionExternalInitiatorKey`-based check (`auth.GetAuthenticatedExternalInitiator`) in `PipelineRunsController.Create` to reject the numeric-ID path when the request is EI-authenticated, regardless of what `GetAuthenticatedUser` returns. Alternatively, tag the synthetic EI user with a distinct marker/role that `Create` explicitly checks against.

### Proof of Concept
1. Handler-level integration test using `cltest` helpers: register an External Initiator with valid access key/secret, and a job with known numeric `job.ID`.
2. Issue `POST /v2/jobs/<numericJobID>/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`) instead of session cookie or API token headers.
3. Assert that the response is `200`/pipeline run created (demonstrating `RunJobV2` was invoked), and that `auth.GetAuthenticatedUser` returned `ok=true` for this EI-authenticated context (add a unit test directly on `AuthenticateExternalInitiator` + `GetAuthenticatedUser` asserting `isUser == true` after EI auth, contradicting the doc comment's intended restriction).
4. Expected (fixed) behavior: the numeric-ID branch should return `422`/"bad job ID" or `401` for EI-authenticated requests, matching the comment's stated intent.

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
