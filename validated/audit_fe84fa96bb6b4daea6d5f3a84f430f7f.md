### Title
EI credential allows triggering job runs for ANY job, not just its own bound webhook job - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` grants any successfully-authenticated External Initiator (EI) the same session privileges as a full application user with `UserRoleRun`, without recording or checking which job(s) that EI is permitted to trigger. `PipelineRunsController.Create` then treats any authenticated "user" (including this synthetic EI-derived user) identically, calling `RunJobV2` for whatever integer job ID is supplied in the URL, with no verification that the job belongs to, or is bound to, the authenticating EI.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) validates the EI's AccessKey/Secret against the stored `ExternalInitiator` record via `bridges.AuthenticateExternalInitiator` (`core/bridges/external_initiator.go:61-67`), which only performs a constant-time HMAC/secret comparison — it never looks up or validates which job(s) this EI is authorized to run. On success it unconditionally sets:
```go
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

`PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-128`) then checks only whether *any* authenticated user is present via `auth.GetAuthenticatedUser(c)`:
```go
_, isUser := auth.GetAuthenticatedUser(c)
if isUser {
    jobID64, err := strconv.ParseInt(idStr, 10, 32)
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [2](#0-1) 

Because `AuthenticateExternalInitiator` populates the same `SessionUserKey` used by real logged-in/token users, `isUser` is `true` for EI-authenticated requests too, so an EI credential reaches the exact same `RunJobV2(ctx, jobID, nil)` call path as a privileged dashboard user — for **any** integer job ID supplied in the path, not just a job that references that specific EI. Neither `AuthenticateExternalInitiator` nor `Create` consults the target job's spec (e.g., any webhook/EI binding) to confirm the calling EI is authorized for that particular job ID.

### Impact Explanation
Any holder of a single valid EI AccessKey/Secret pair can enumerate integer job IDs and POST to `/v2/jobs/:ID/runs` to trigger job runs belonging to other EIs or unrelated webhook jobs on the same node, bypassing the intended per-EI request-binding model. This is unauthorized job triggering / request impersonation across principals sharing a node, matching the "unauthorized job run" bounty impact class — particularly severe if the triggered job pipeline has side effects (on-chain transactions, external HTTP calls, secret-bearing bridge invocations).

### Likelihood Explanation
The only precondition is possession of one valid, unprivileged EI AccessKey/Secret pair (the exact "unprivileged attacker" credential class in scope). No knowledge of other EIs' secrets is needed — only the numeric job ID, which is small and easily enumerable (`ParseInt(idStr, 10, 32)`). The exploit is fully reproducible via a single authenticated HTTP POST and requires no timing tricks or race conditions.

### Recommendation
In `PipelineRunsController.Create`, distinguish between a real user session and an EI-derived session (e.g., via `auth.GetAuthenticatedExternalInitiator(c)`), and when the caller is an EI, look up the job's spec to confirm it is bound to that specific EI (matching by name/ID as job specs intend) before calling `RunJobV2`. Do not let `AuthenticateExternalInitiator` silently satisfy the same `isUser` check as genuine user sessions without this binding check.

### Proof of Concept
1. Create two External Initiators, `EI-A` and `EI-B`, each with distinct AccessKey/Secret pairs, via the EI creation API.
2. Create two webhook jobs, `Job-A` (intended for `EI-A`) and `Job-B` (intended for `EI-B`), each with distinct integer job IDs.
3. Authenticate as `EI-A` (set `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers for EI-A).
4. POST `/v2/jobs/<Job-B-ID>/runs` using EI-A's credentials.
5. Assert: expected behavior is `401 Unauthorized` / `403 Forbidden` / `422 Unprocessable Entity` (job not bound to this EI); actual current behavior is `200 OK` with a created pipeline run resource, confirming `RunJobV2` executed `Job-B` under `EI-A`'s credentials.
6. As a control, run the same test for `EI-A` triggering `Job-A` (its own job) to confirm the endpoint's baseline success path is unaffected — isolating the escalation to cross-EI job triggering.

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
