### Title
External Initiator credential holder can trigger runs on ANY job by int ID, bypassing the "EIs not allowed" restriction and any EI-to-job binding - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the generic `SessionUserKey` to `&clsessions.User{Role: UserRoleRun}` in addition to `SessionExternalInitiatorKey`, so any code that checks `auth.GetAuthenticatedUser` cannot distinguish an external-initiator-authenticated request from a real user session. `PipelineRunsController.Create` relies on exactly this check (`isUser`) to decide whether to run a job by int ID, and its own comment ("EIs not allowed") is therefore not enforced. Combined with `RunJobV2` taking only the raw int32 job ID with no binding to the calling EI, any holder of a single EI's access key/secret can trigger a run for any job on the node, not just a job tied to that EI.

### Finding Description
The route is `userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))`. `RequiresRunRole` only checks `user.Role != UserRoleView` [1](#0-0) , it performs no identity/job-binding check.

`AuthenticateExternalInitiator` authenticates the EI's access key/secret, then unconditionally sets a synthetic user object on the same context key used for real user sessions: [2](#0-1) 

`PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide `isUser`, intending "only users are allowed to run jobs using int IDs - EIs not allowed": [3](#0-2) 

Because `GetAuthenticatedUser` simply reads `SessionUserKey` [4](#0-3) , and that key is populated for EI-authenticated requests too, `isUser` evaluates to `true` for an EI caller. The handler then parses the URL `:ID` as an int32 and calls `prc.App.RunJobV2(ctx, jobID, nil)` with no check that the job is bound to, or associated with, the specific external initiator (`ei`) that authenticated the request. `GetAuthenticatedExternalInitiator` and the `ei` object obtained during authentication are never consulted inside `Create` to validate the ID-to-EI relationship.

Consequently, an attacker holding valid credentials for EI 'A' can send `POST /v2/jobs/:ID/runs` with the numeric ID of a job that has nothing to do with EI 'A' (including one intended only for EI 'B', or a completely unrelated job such as a cron/VRF/keeper job), and the request will be processed as an authorized run.

### Impact Explanation
This is an authorization-bypass leading to unauthorized job execution: a low-privilege external-initiator credential (typically issued to a narrowly-scoped off-chain data source) can invoke pipeline runs for arbitrary jobs on the node. Depending on job configuration this can cause unwanted on-chain transactions/gas expenditure, spurious oracle report submissions, or repeated-run resource exhaustion (DoS) against jobs the EI credential was never meant to trigger — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
The only precondition is possession of one valid, low-privilege External Initiator access key/secret pair (an unprivileged, narrowly-scoped credential by design). No admin/session/API-token access, no knowledge of other EIs' credentials, and no host access are required — the attacker only needs to know or guess a numeric job ID, which is enumerable/sequential in this API. The attack is trivially repeatable (simple HTTP POST).

### Recommendation
In `PipelineRunsController.Create` (and any other `userOrEI`-guarded handler relying on `GetAuthenticatedUser`), explicitly distinguish EI-authenticated requests from real user sessions (e.g., check `GetAuthenticatedExternalInitiator` first and reject/route separately), and, if EI-triggered runs by int ID are meant to be supported at all, validate that the target job is actually bound to the authenticated EI (e.g., via the job's `ExternalJobID`/external initiator spec) before calling `RunJobV2`. At minimum, stop `AuthenticateExternalInitiator` from silently satisfying `GetAuthenticatedUser` checks meant for real users.

### Proof of Concept
Handler-level integration test plan:
1. Create two external initiators, EI-A and EI-B, via `bridges.ExternalInitiator` fixtures, each with distinct access key/secret.
2. Create Job-B (any non-webhook job type with numeric ID) that is conceptually associated/created for EI-B's workflow.
3. Send `POST /v2/jobs/:ID/runs` with `ID` = Job-B's int32 ID, using headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to EI-A's credentials (not EI-B's).
4. Assert the response is `200 OK` with a pipeline run resource returned (current behavior) — demonstrating the bypass, since the request should instead be rejected with `401/403` for lacking any binding to Job-B.
5. Add assertion that `GetAuthenticatedExternalInitiator(c).ID` (EI-A) is never compared against the job's owning EI anywhere in `PipelineRunsController.Create`, confirming the missing binding check identified above.

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
