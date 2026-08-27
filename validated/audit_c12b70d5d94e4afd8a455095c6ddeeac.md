### Title
External Initiator can trigger runs for jobs bound to a different External Initiator via generic run-creation route - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` and `SessionUserKey` (with `Role: UserRoleRun`) on successful EI authentication. `PipelineRunsController.Create()` uses `auth.GetAuthenticatedUser(c)` to gate the generic `POST /jobs/:ID/runs` route, intending (per its own comment) to disallow EIs from using this path, but that check only tests for `SessionUserKey` presence — which is always true for a successfully authenticated EI too. The handler then calls `prc.App.RunJobV2(ctx, jobID, nil)` directly by job ID with no check of `SessionExternalInitiatorKey`'s `Name` against the job's bound initiator.

### Finding Description
`auth.AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) on success does:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

This means any request authenticated as an external initiator also has `SessionUserKey` populated with a synthetic run-role user.

`PipelineRunsController.Create()` (`core/web/pipeline_runs_controller.go:89-128`) gates access with:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [2](#0-1) 

Because `GetAuthenticatedUser` (`core/web/auth/auth.go:178-187`) simply reads `SessionUserKey` regardless of how it was populated, `isUser` evaluates to `true` for both real dashboard/API-token users AND for EI-authenticated requests (since EI auth also sets `SessionUserKey`). The comment's stated intent ("EIs not allowed") is not actually enforced by this check.

Critically, the handler never inspects `SessionExternalInitiatorKey` nor compares `ei.Name` to the job's configured external-initiator binding before calling `prc.App.RunJobV2(ctx, jobID, nil)`. The job is looked up and executed purely by its numeric `ID`, with no ownership/binding validation of which EI is allowed to trigger it. Thus an EI credentialed as 'A' can hit `POST /v2/jobs/:jobID/runs` with the numeric ID of a job that is configured to be triggered only by EI 'B', and the run will execute.

### Impact Explanation
This allows unauthorized run creation for a job that is not bound to the authenticated external initiator's name — an authorization/request-binding bypass (`REQUEST_BINDING` invariant) enabling any credentialed EI to trigger runs (and therefore any downstream on-chain/off-chain actions those runs perform, e.g. fund movement, VRF/OCR triggers, oracle responses) for any job in the node reachable by numeric ID, not just the job it is provisioned for.

### Likelihood Explanation
Requires only valid credentials for one legitimate external initiator (low privilege, non-admin) plus knowledge/guessing of another job's numeric ID (job IDs are sequential integers and can plausibly be enumerated or observed). No additional session, admin, or host access is needed. This is fully reachable from the standard node HTTP API.

### Recommendation
In `PipelineRunsController.Create()`, explicitly reject requests where `auth.GetAuthenticatedExternalInitiator(c)` returns a set EI (i.e., check for absence of `SessionExternalInitiatorKey`, not just presence of `SessionUserKey`), or, if EIs are meant to be able to use this route, load the target job's configured external initiator and verify `ei.Name` matches the job's binding before invoking `RunJobV2`. Also remove the accidental double `c.Set(SessionExternalInitiatorKey, ei)` call in `AuthenticateExternalInitiator`, and avoid overloading `SessionUserKey` for EI identity — use a distinct check (e.g., `_, isEI := auth.GetAuthenticatedExternalInitiator(c)`) rather than relying on the shared `SessionUserKey`.

### Proof of Concept
1. Handler-level integration test: authenticate a gin context via `AuthenticateExternalInitiator` for EI "A" credentials.
2. Call `PipelineRunsController.Create()` with `c.Param("ID")` set to the numeric ID of a job configured with an external-initiator spec bound to EI "B".
3. Assert current behavior: `isUser` is `true` (via `auth.GetAuthenticatedUser(c)`), `prc.App.RunJobV2` is invoked and returns 200 with a created pipeline run — demonstrating the bypass.
4. Add a regression assertion: after fix, the same request should return `401/403` because `ei.Name != job.ExternalInitiatorSpec.Name`, verified via a unit test asserting `RunJobV2` is never called when `GetAuthenticatedExternalInitiator(c)` is set and its `Name` doesn't match the job's binding.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
```

**File:** core/web/pipeline_runs_controller.go (L109-123)
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
```
