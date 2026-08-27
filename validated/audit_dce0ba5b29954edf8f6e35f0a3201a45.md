Based on the code I found, here's the analysis:

### Title
Broken Object-Level Authorization: External Initiator credentials can trigger runs on unrelated jobs via `POST /v2/jobs/:ID/runs` - ([File: core/web/router.go])

### Summary
The `userOrEI` route group in `core/web/router.go` chains `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, and `auth.AuthenticateBySession` before `auth.RequiresRunRole(prc.Create)`. A successful EI authentication injects a synthetic `clsessions.User{Role: UserRoleRun}` into the Gin context, which trivially satisfies `RequiresRunRole`'s role check and is indistinguishable, from `PipelineRunsController.Create`'s perspective, from a legitimately privileged human user.

### Finding Description
`AuthenticateExternalInitiator` in [1](#0-0)  authenticates the EI by its access key/secret and unconditionally sets `SessionUserKey` to `&clsessions.User{Role: clsessions.UserRoleRun}` — a synthetic user object with no reference to which EI authenticated or which job(s) it is permitted to run.

`RequiresRunRole` in [2](#0-1)  only checks `user.Role == clsessions.UserRoleView` to reject; any non-View role (including the synthetic `UserRoleRun` from EI auth) passes through to the handler.

In `PipelineRunsController.Create` ( [3](#0-2) ), the code does:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
```
The comment states "EIs not allowed," but `auth.GetAuthenticatedUser(c)` reads the same `SessionUserKey` context value that `AuthenticateExternalInitiator` populates with the synthetic user — so `isUser` is `true` for EI-authenticated requests too, contradicting the intended restriction. Crucially, `jobID` is taken directly from `c.Param("ID")` with **no check that the specific External Initiator which authenticated is bound to, registered for, or otherwise associated with that job ID**. There is no lookup of an `ExternalInitiatorWebhookSpec`-style binding table filtering by EI name/ID before invoking `RunJobV2`.

### Impact Explanation
An attacker holding valid credentials for *any* registered External Initiator (e.g., one legitimately provisioned for job "foo") can call `POST /v2/jobs/:ID/runs` with an arbitrary integer job ID belonging to a completely unrelated job "bar" and trigger a pipeline run on it, since no code path verifies EI-to-job association at this route. This is an authorization bypass allowing unauthorized triggering of job runs (potential unauthorized on-chain fund movement or off-chain side effects) across job boundaries, matching Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
The attacker only needs the `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` pair for any single registered EI (the minimal, lowest-tier credential in the system, provisioned via `EIRequiresEditRole` for administrative convenience) and knowledge of a job's integer ID. Job IDs are typically small sequential integers and can plausibly be enumerated or already known from earlier interactions. The attack is trivially repeatable and requires no session, token, or admin access.

### Recommendation
- Bind External Initiator authentication to specific job(s)/webhook spec(s) it was created for, and check that binding inside `PipelineRunsController.Create` (or in a dedicated auth middleware) before calling `RunJobV2`.
- Fix the `isUser` check in `Create` to actually exclude EI-authenticated requests as the comment intends (e.g., by checking `auth.GetAuthenticatedExternalInitiator(c)` is absent) if EIs are truly meant to be excluded from this int-ID path, or if EIs are meant to be allowed, add explicit job-to-EI ownership verification.

### Proof of Concept
Go handler-level integration test plan:
1. Register `ExternalInitiator` named `"foo"` with its own access key/secret, tied to a job spec/webhook named "foo" (if such a binding model exists in the ORM).
2. Create a second, unrelated job `"bar"` (V2 job, e.g., OCR or DirectRequest) with integer ID `barID`, with no relation to EI "foo".
3. Send `POST /v2/jobs/{barID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI "foo"'s credentials.
4. Assert: expected behavior (secure) — response should be `401/403` due to missing initiator-to-job binding.
5. Actual behavior (per code review): request passes `auth.AuthenticateExternalInitiator` → sets synthetic `UserRoleRun` → passes `RequiresRunRole` → `isUser` is true in `Create` → `prc.App.RunJobV2(ctx, barID, nil)` is invoked, returning `200 OK` with a run created for job "bar," proving the missing binding check.

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
