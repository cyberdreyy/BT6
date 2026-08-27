### Title
External-Initiator credentials bypass job binding and can trigger runs on arbitrary jobs via integer job IDs - ([File: core/web/pipeline_runs_controller.go])

### Summary
`auth.AuthenticateExternalInitiator` authenticates an EI credential and then stores a generic `clsessions.User{Role: UserRoleRun}` under the same `SessionUserKey` used for regular session/token users, without recording which specific job the EI is bound to. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` merely to check "is *any* user object present in context" (`isUser`), which is true for EI-authenticated requests too, and if the target `:ID` parses as an int32 it directly calls `prc.App.RunJobV2(ctx, jobID, nil)` without any check binding the credential (EI or user) to that specific job.

### Finding Description
The middleware chain is:
- `AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) looks up the EI purely by `AccessKey`/`Secret` via `store.FindExternalInitiator`/`bridges.AuthenticateExternalInitiator`, with no job association. On success it sets:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

- `RequiresRunRole` (`core/web/auth/auth.go:202-217`) only checks `user.Role != UserRoleView`, so both real users and EI-authenticated requests pass. [2](#0-1) 

- `PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-128`) contains this logic:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    jobID, err := strconv.ParseInt(idStr, 10, 32)
    if err == nil {
        jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
        ...
    }
}
``` [3](#0-2) 

The comment states "EIs not allowed" for integer job IDs, but `auth.GetAuthenticatedUser` (`core/web/auth/auth.go:178-187`) simply reads whatever object was stored under `SessionUserKey` — it cannot distinguish a real authenticated user from the generic placeholder `&clsessions.User{Role: UserRoleRun}` set by `AuthenticateExternalInitiator`. Consequently `isUser` evaluates to `true` for EI-authenticated requests as well, and `RunJobV2` is invoked with attacker-supplied `jobID` taken directly from the URL `:ID` path parameter — with no verification that the authenticated EI (or any EI) is associated with that job at all. [4](#0-3) 

The only remaining gate is the `uuid.Parse(idStr)` check that rejects UUID-formatted IDs (the legacy webhook-job binding path), leaving the integer-ID path open to any credential holder whose auth middleware sets `SessionUserKey`, EI included.

### Impact Explanation
An attacker holding one valid EI AccessKey/Secret pair (issued for job A) can invoke `POST /v2/jobs/<jobB_ID>/runs` for any job B in the node addressed by its numeric ID, not just jobs the EI was created for. This breaks the request-binding invariant that an EI credential must only trigger runs on jobs it is authorized for, allowing unauthorized triggering of pipeline runs across job boundaries — matching Chainlink's "unauthorized job run" impact class, since it lets an unprivileged/limited credential holder invoke node execution and side effects (e.g. bridge calls, on-chain transmissions) belonging to unrelated jobs.

### Likelihood Explanation
Preconditions are minimal: possession of one valid, legitimately obtained EI AccessKey/Secret pair (which is often distributed to lower-trust external systems). No additional role or admin access is required. The request is a single unauthenticated-relative-to-job-B HTTP POST with standard `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and the target job's integer ID (obtainable from `/v2/jobs` listing or other disclosure). This is straightforward and repeatable.

### Recommendation
- In `PipelineRunsController.Create`, distinguish EI-authenticated requests from real user sessions/tokens (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` presence and reject the integer-ID path entirely for EI-authenticated requests, consistent with the existing code comment's intent).
- If EI-triggered runs must be supported for a job, explicitly bind the `ExternalInitiator` to the job (e.g., verify the job's `ExternalInitiatorSpec` references the authenticated EI's name/ID) before calling `RunJobV2`.

### Proof of Concept
Handler-level integration test plan:
1. Create two jobs, Job A (with an associated External Initiator `EI1`) and Job B (a plain OCR/direct-request job with a distinct owner/no EI relation), via `cltest.CreateExternalInitiatorViaWeb` and `AddJobV2`.
2. Authenticate as `EI1` using its `AccessKey`/`Secret` (`X-Chainlink-EA-AccessKey`, `X-Chainlink-EA-Secret` headers).
3. Send `POST /v2/jobs/<JobB.ID>/runs` (integer ID) using `EI1`'s credentials.
4. Assert current behavior: response is `200/201` with a `pipelineRun` resource for Job B, and `pipeline_runs` table gains a row for Job B — proving `EI1` triggered a run on an unrelated job it was never bound to.
5. Expected/fixed behavior: response should be `401 Unauthorized` or `403 Forbidden`, and no new `pipeline_runs` row should exist for Job B.

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
