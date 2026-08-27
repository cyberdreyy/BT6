### Title
Identity confusion between EI and User auth allows external-initiator credentials to trigger arbitrary job runs by ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` determines whether a caller is allowed to run a job by integer ID using `auth.GetAuthenticatedUser(c)`, but `AuthenticateExternalInitiator` also populates the same `SessionUserKey` context value with a synthetic `clsessions.User{Role: clsessions.UserRoleRun}`. As a result, `isUser` is `true` for external-initiator (EI) authenticated requests too, directly contradicting the code's own comment that "EIs not allowed," and letting an EI credential holder run any job by its integer ID via `prc.App.RunJobV2`.

### Finding Description
`Create` gates integer-ID job execution on: [1](#0-0) 

`isUser` is obtained from `auth.GetAuthenticatedUser(c)`, which simply reads whatever value was stashed under `SessionUserKey` in the gin context — it has no way to distinguish "real" user-authenticated sessions from EI-authenticated ones: [2](#0-1) 

However, `AuthenticateExternalInitiator` — the auth method used for external-initiator credentials (`X-External-Initiator-Access-Key` / `X-External-Initiator-Secret` headers) — explicitly writes a fake `User` object with `Role: clsessions.UserRoleRun` into `SessionUserKey`: [3](#0-2) 

Because `GetAuthenticatedUser` only checks for the presence/type of the `SessionUserKey` value, not its provenance, any request authenticated via EI credentials will have `isUser == true` in `Create`, and the handler will proceed to parse the path `:ID` as an int32 job ID and call `prc.App.RunJobV2(ctx, jobID, nil)` for that arbitrary ID — with no restriction to jobs that "belong" to that EI or to webhook-type jobs. The in-code comment ("only users are allowed to run jobs using int IDs - EIs not allowed") demonstrates the intended invariant, but the implementation fails to enforce it because it conflates the "user" role object with a genuine authenticated user identity.

### Impact Explanation
An external-initiator credential holder — a lower-privilege, narrowly-scoped credential intended only to trigger the specific job(s) it's registered against — can invoke arbitrary job runs on the node by guessing/enumerating integer job IDs, corresponding to Chainlink's "unauthorized job run" bounty impact class. Depending on the job's tasks (e.g., ETH tx tasks, bridge calls), this could cause unintended on-chain transactions, resource exhaustion, or triggering of jobs unrelated to the EI's intended scope — an authorization/role bypass and identity confusion between two distinct auth mechanisms.

### Likelihood Explanation
Preconditions are minimal: only a valid external-initiator access key/secret pair (already a lower-privilege credential by design) is required — no admin/user session or edit/admin role is needed. The attack is a single HTTP POST to `/v2/jobs/:ID/runs` with an arbitrary integer, fully repeatable, and requires no timing race or complex chaining.

### Recommendation
Do not reuse `SessionUserKey`/`GetAuthenticatedUser` to represent EI-derived identities. Introduce a distinct marker (already partially present via `SessionExternalInitiatorKey`) and have `Create` explicitly reject requests where `GetAuthenticatedExternalInitiator(c)` is set (or where the "user" object is the synthetic Run-role placeholder), rather than relying on the ambiguous `isUser` boolean. Alternatively, tag the synthetic user object with a distinguishing field (e.g., `IsExternalInitiator: true`) and have `Create` check that flag before allowing int-ID job runs.

### Proof of Concept
Go handler-level integration test:
1. Set up a test app/router with a registered external initiator (`FindExternalInitiator` returns a valid EI) and a job (any non-webhook type, e.g., OCR/cron) with a known integer ID.
2. Send `POST /v2/jobs/<jobID>/runs` with headers `X-External-Initiator-Access-Key`/`X-External-Initiator-Secret` set to the EI's valid credentials (no user session/API token headers).
3. Assert the middleware chain invokes `AuthenticateExternalInitiator`, which sets `SessionUserKey` to `&clsessions.User{Role: UserRoleRun}`.
4. Assert `auth.GetAuthenticatedUser(c)` returns `ok == true` inside `Create`, so `isUser == true`.
5. Assert the response is `200 OK` with a `pipelineRun` resource (i.e., `prc.App.RunJobV2` was actually invoked) rather than the expected `422 bad job ID` rejection for EI-only credentials.
6. Compare against a control case using genuine session/API-token auth to show both paths reach the same code with no differentiation.

### Citations

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
