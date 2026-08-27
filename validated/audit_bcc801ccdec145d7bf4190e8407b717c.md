### Title
Cross-initiator job-run impersonation via synthetic user injection in `AuthenticateExternalInitiator` bypasses "EIs not allowed" check in `PipelineRunsController.Create` - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` authenticates the EI credential pair but then injects a synthetic `clsessions.User{Role: UserRoleRun}` into the request context under the same `SessionUserKey` used for real user sessions. `PipelineRunsController.Create` gates its numeric-job-ID run-trigger path solely on `auth.GetAuthenticatedUser(c)` returning `isUser == true`, with a comment claiming "EIs not allowed" — but because the EI auth path also sets a fake user, any valid EI credential passes this check and can trigger a run on **any** integer job ID, not just jobs tied to that specific initiator.

### Finding Description
The middleware `AuthenticateExternalInitiator` validates the `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` pair against `store.FindExternalInitiator`, then stores the EI object under `SessionExternalInitiatorKey` — but it also does: [1](#0-0) 

This line unconditionally sets `SessionUserKey` to a synthetic `User{Role: UserRoleRun}` regardless of which initiator authenticated.

`PipelineRunsController.Create` (handler for `POST /v2/jobs/:ID/runs`) then checks: [2](#0-1) 

The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but `auth.GetAuthenticatedUser(c)` returns `true` for *both* real user sessions and EI-authenticated requests, because `AuthenticateExternalInitiator` injects the same `SessionUserKey`. The handler never calls `auth.GetAuthenticatedExternalInitiator(c)` to check which initiator authenticated, nor does it verify that the target job's `ExternalInitiatorWebhookSpec` (or equivalent binding) matches the authenticated initiator. It only parses the path `:ID` as an int32 and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly.

Consequently, an attacker holding valid credentials for external initiator A can authenticate via the EI headers and then issue `POST /v2/jobs/<jobID belonging to initiator B or any user>/runs`, and the request passes the `isUser` gate and triggers `RunJobV2` for that arbitrary job ID — with no binding of the authenticated identity to the specific job/initiator in the path.

### Impact Explanation
This is an authorization/isolation-boundary violation: a holder of a single external initiator's low-privilege credential can trigger execution of pipeline runs for jobs it does not own or was not authorized to invoke, across initiator boundaries. Depending on job composition, this can trigger unauthorized bridge/task execution, fund-relevant actions, or unexpected on-chain writes tied to another initiator's job — a cross-user job-run triggering / isolation violation matching Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
Preconditions are minimal: attacker only needs one valid EI AccessKey/Secret pair (already an "unprivileged" credential class per the audit scope). No further privilege escalation, session token, or admin access is required. The flow is fully reachable over the public HTTP API (`POST /v2/jobs/:ID/runs`) with no rate-limiting or additional identity binding, making it trivially repeatable.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject or restrict EI-authenticated callers by checking `auth.GetAuthenticatedExternalInitiator(c)` in addition to (or instead of) `GetAuthenticatedUser(c)`, and if EI-triggered runs on integer job IDs are intended to be supported at all, verify that the authenticated `ExternalInitiator`'s ID matches the target job's associated initiator (e.g., via the job's `ExternalInitiatorWebhookSpec` or equivalent ownership record) before calling `RunJobV2`. Alternatively, stop `AuthenticateExternalInitiator` from injecting a generic `SessionUserKey` value that is indistinguishable from a real user session, so downstream handlers can reliably differentiate EI-authenticated requests from user-authenticated ones.

### Proof of Concept
Handler-level integration test plan (Go, using `httptest`/gin test harness consistent with `pipeline_runs_controller_test.go`):
1. Create two external initiators, `eiA` and `eiB`, each with distinct AccessKey/Secret via the bridges ORM.
2. Create `jobA` associated with `eiA` and `jobB` associated with `eiB` (or simply any job owned by a different actor context).
3. Authenticate an HTTP client using `eiA`'s `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers.
4. Issue `POST /v2/jobs/<jobB.ID>/runs` with that client.
5. Assert expected behavior: request should be rejected (`401`/`403`) because `eiA` is not authorized for `jobB`.
6. Actual current behavior: `auth.GetAuthenticatedUser(c)` returns the synthetic `User{Role: UserRoleRun}` set by `AuthenticateExternalInitiator`, `isUser` is `true`, `jobB.ID` parses as int32, and `prc.App.RunJobV2(ctx, jobB.ID, nil)` executes successfully, returning `200` with a `pipelineRun` resource — confirming the cross-initiator bypass.

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
