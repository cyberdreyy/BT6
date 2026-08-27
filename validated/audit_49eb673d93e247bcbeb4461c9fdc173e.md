### Title
External Initiator credentials bypass job-scoping and can trigger `RunJobV2` for arbitrary job IDs - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
`PipelineRunsController.Create` decides whether to run a job by int ID solely based on `isUser` returned from `auth.GetAuthenticatedUser(c)`: [1](#0-0) 

However, `AuthenticateExternalInitiator` — the auth method used for EI (External Initiator) credentials — explicitly stores a **synthetic** `*clsessions.User{Role: clsessions.UserRoleRun}` under `SessionUserKey`, the exact same key used by real session/token-based user authentication: [2](#0-1) 

`GetAuthenticatedUser` blindly type-asserts whatever is stored at `SessionUserKey`, without distinguishing whether it came from an actual DB-backed user (`AuthenticateBySession`/`AuthenticateByToken`) or from the EI synthetic placeholder: [3](#0-2) 

Because `AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` and `SessionUserKey`, `GetAuthenticatedUser` returns `(user, true)` for a pure EI request. This makes `isUser == true` in `PipelineRunsController.Create`, which is documented as being reserved for "only users ... EIs not allowed" (the inline comment at line 110 confirms this is the intended security boundary). The check trusts the wrong signal (`isUser`) instead of checking `GetAuthenticatedExternalInitiator` explicitly to reject EI-originated identities, or validating that the EI is bound to the requested `jobID`.

As a result, an attacker holding only valid EI access-key/secret headers (`static.ExternalInitiatorAccessKeyHeader` / `static.ExternalInitiatorSecretHeader`) can call `POST /v2/jobs/:ID/runs` with **any** numeric job ID and have `prc.App.RunJobV2(ctx, jobID, nil)` executed for that job — not just the job/bridge the EI was registered for. There is no lookup tying the authenticated `ExternalInitiator` to the specific `jobID` in the request path.

### Impact Explanation
This is an authorization-exactness violation: EI credentials, which are meant to be scoped to triggering runs for jobs associated with a specific bridge/webhook, can instead run **any** job on the node by simply guessing/enumerating small integer job IDs. This can cause unauthorized execution of pipelines (e.g., triggering on-chain transactions, spec side effects, consuming task resources) that the EI was never entitled to invoke — matching the "Unauthorized job run / authorization bypass" bounty impact class.

### Likelihood Explanation
Exploitation only requires valid EI credentials (access key + secret) for any single external initiator already registered on the node — no admin/session/API-token access, and no knowledge of which jobs that EI is normally tied to. Job IDs are small sequential integers, making enumeration trivial. The bypass is deterministic and repeatable on every request.

### Recommendation
In `PipelineRunsController.Create`, do not rely on `isUser` alone. Explicitly reject requests where `auth.GetAuthenticatedExternalInitiator(c)` succeeds (i.e., the identity originated from EI auth), or alternatively check the concrete session/token authentication method rather than a synthesized user. If EI-triggered runs by int job ID are intended to be supported, they must validate that the job's spec is actually a webhook/bridge bound to that specific `ExternalInitiator` before calling `RunJobV2`.

### Proof of Concept
Handler-level integration test in `core/web/pipeline_runs_controller_test.go`:
1. Create two jobs, `jobA` (owned/bound to external initiator `EI1`) and `jobB` (unrelated to `EI1`).
2. Authenticate solely using `EI1`'s access key/secret headers (`static.ExternalInitiatorAccessKeyHeader`, `static.ExternalInitiatorSecretHeader`), with no session cookie and no `X-API-KEY`/`X-API-SECRET`.
3. `POST /v2/jobs/{jobB.ID}/runs`.
4. Current (vulnerable) behavior: request succeeds, `App.RunJobV2` is invoked with `jobB.ID`, response 200 with a `pipelineRun` resource.
5. Expected/fixed assertion: request should be rejected with `401`/`422` (EI not authorized for `jobB`), or `RunJobV2` should never be invoked for a job not associated with the authenticated `ExternalInitiator`.

### Citations

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
