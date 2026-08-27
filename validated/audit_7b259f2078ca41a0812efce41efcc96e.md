### Title
External Initiator credential holder can trigger runs on ANY job by numeric ID, bypassing EI-to-job binding check - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` and a synthetic `SessionUserKey` (with `UserRoleRun`) in the gin context [1](#0-0) . `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the request is a "user" allowed to run jobs by integer ID, explicitly commenting that "EIs not allowed" [2](#0-1) , but because the EI auth path also populates `SessionUserKey`, this check is bypassed for EI-authenticated requests, letting an external initiator invoke `RunJobV2` on any job ID without any check that the job has an `ExternalInitiatorWebHookSpec` bound to that initiator.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is guarded by `auth.Authenticate(..., auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` followed by `auth.RequiresRunRole(prc.Create)` [3](#0-2) .

When authenticated via a valid EI access key/secret pair, `AuthenticateExternalInitiator` does:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

`RequiresRunRole` only checks that the role is not `UserRoleView`, so the synthetic Run-role user passes [4](#0-3) .

Inside `PipelineRunsController.Create`, the code explicitly intends to restrict integer-ID job runs to "users" only, excluding EIs:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [2](#0-1) 

However, because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `auth.GetAuthenticatedUser(c)` returns `ok=true` even for an EI-authenticated request — the `isUser` guard is a no-op for EI credentials. The handler never inspects `c.Get(SessionExternalInitiatorKey)` nor cross-checks it against the target job's `ExternalInitiatorWebHookSpec`. Consequently, any job accessible by a plain integer ID (cron, webhook-authenticated-by-session, or any other job type) can be triggered by an EI credential that has zero binding to that job.

### Impact Explanation
This is an authorization bypass allowing an external-initiator credential (a low-privilege, narrowly-scoped credential meant only to trigger the specific webhook job it is registered against) to trigger runs of arbitrary jobs on the node, including jobs that were never intended to be externally triggerable. Depending on job pipeline tasks (e.g., ETH tx tasks, bridge calls), this can cause unauthorized job execution, resource exhaustion, or unintended on-chain transactions/fund movement — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Requires only possession of any valid external initiator's access key/secret (a credential explicitly designed to be distributable to third-party integrators, hence lower trust than admin/edit credentials). No job-specific binding is needed. The attack is a single unauthenticated-by-role HTTP POST once EI credentials are obtained — fully repeatable and deterministic given the code path shown above.

### Recommendation
In `PipelineRunsController.Create`, explicitly check for the presence of an authenticated External Initiator via `auth.GetAuthenticatedExternalInitiator(c)` and reject (or separately validate against the job's `ExternalInitiatorWebHookSpec`) before allowing `RunJobV2` by integer ID. Do not rely solely on `auth.GetAuthenticatedUser` since `AuthenticateExternalInitiator` also sets a synthetic user object. Alternatively, stop setting `SessionUserKey` in `AuthenticateExternalInitiator`, and have call sites use `GetAuthenticatedExternalInitiator` explicitly wherever EI-specific authorization logic is required.

### Proof of Concept
1. Register an external initiator `EI-A` with access key/secret, not bound to any job (no `ExternalInitiatorWebHookSpec` row referencing it).
2. Create job `J` (e.g. a cron job or webhook job authenticated by session) with integer ID `123`, unrelated to `EI-A`.
3. Send `POST /v2/jobs/123/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `EI-A`'s credentials.
4. Expected (per intended design, per code comment "EIs not allowed"): `403`/`422` rejection.
5. Actual: request passes `RequiresRunRole`, `isUser` evaluates true (due to synthetic user), and `prc.App.RunJobV2(ctx, 123, nil)` is invoked, returning `200` with a created pipeline run — demonstrating the unauthorized run trigger.

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

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```
