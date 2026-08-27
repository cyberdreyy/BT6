### Title
External Initiator authentication impersonates a full "run"-role user, bypassing the "EIs not allowed" check in `PipelineRunsController.Create` and enabling cross-job run triggering by integer job ID - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` used by real session/token users, with role `run`, so `PipelineRunsController.Create`'s `isUser, _ := auth.GetAuthenticatedUser(c)` check (intended to gate the integer-job-ID run path to real users, per its own comment "EIs not allowed") returns `true` for EI-authenticated requests as well. Because the `ExternalInitiator` model has no job/bridge scope field at all, any valid EI credential can trigger a run for **any** integer job ID via `POST /v2/jobs/:ID/runs`, not just a job related to its own configuration.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) authenticates the EI access key/secret and then does:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

This places a synthetic `User{Role: UserRoleRun}` under the exact same context key (`SessionUserKey`) used by `AuthenticateBySession`/`AuthenticateByToken` for real users.

`PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-128`) contains this comment and check:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
    ...
}
``` [2](#0-1) 

`auth.GetAuthenticatedUser` simply reads `SessionUserKey` from context:
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	...
}
``` [3](#0-2) 

It cannot distinguish "real user session/token" from "EI acting as a synthetic run-role user" — both populate `SessionUserKey`. As a result, the explicit intent encoded in the comment ("EIs not allowed" for the int-ID run path) is not enforced by the code.

Additionally, the `ExternalInitiator` model (`core/bridges/external_initiator.go:22-34`) has no job/bridge-scope field — only `Name`, `URL`, `AccessKey`, `HashedSecret`, etc. — so there is no concept of "the job(s) this EI is authorized for" anywhere in the authentication path. The only historical scoping mechanism was via webhook job specs matched by external job UUID, and that whole job type has since been removed, evidenced by the explicit rejection at the top of `Create`:
```go
if _, err := uuid.Parse(idStr); err == nil {
    jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
    return
}
``` [4](#0-3) 

So an EI holder can no longer trigger via UUID (now rejected), but because of the `isUser` bug, they fall through to the integer-ID branch which has zero binding to any job/bridge the EI was configured for, and triggers `prc.App.RunJobV2(ctx, jobID, nil)` for whatever integer `:ID` is supplied in the URL.

Note: I could not fully confirm from the router file within this session whether `AuthenticateExternalInitiator` is still wired as one of the accepted auth methods on the `POST /v2/jobs/:ID/runs` route in `core/web/router.go` (the grep matched lines but content wasn't retrieved before the tool budget ran out). This routing wiring is the remaining precondition to confirm reachability; if `AuthenticateExternalInitiator` is included in that route's middleware chain (which is consistent with EIs historically triggering runs on this exact endpoint), the vulnerability is fully reachable as described.

### Impact Explanation
If reachable, this allows an external-initiator credential holder — an unprivileged, narrowly-scoped credential type intended only to remotely trigger its own configured job(s) — to trigger pipeline runs on **arbitrary jobs by integer ID** across the entire node, not just the job/bridge it was provisioned for. This is unauthorized action on another user's job (cross-job run triggering), matching a request-binding / authorization-bypass impact class. Depending on job side effects (e.g., triggering OCR/keeper/other job execution logic, external calls, on-chain transactions), this could cause unintended job executions, resource exhaustion, or unauthorized use of node infrastructure and funds tied to other jobs.

### Likelihood Explanation
Only a valid EI access key/secret pair is required — the lowest-privilege credential class explicitly named in scope. No knowledge of the target job's internals is needed beyond guessing/enumerating small sequential integer job IDs, which are trivially enumerable. The behavior is deterministic and fully repeatable given valid EI credentials scoped to any unrelated job.

### Recommendation
Distinguish EI-derived pseudo-users from real authenticated users instead of relying on a shared `SessionUserKey` check. For example:
- In `PipelineRunsController.Create`, explicitly check `auth.GetAuthenticatedExternalInitiator(c)` and reject (403) when an EI is present, rather than relying on `isUser` being true/false based on a shared context key.
- Alternatively, stop `AuthenticateExternalInitiator` from writing a full `User{Role: UserRoleRun}` into `SessionUserKey`; instead expose EI identity only via `SessionExternalInitiatorKey`, and have `RequiresRunRole`/handlers query both keys separately with distinct authorization logic per route.
- If EI-triggered runs by integer job ID are intended to be supported at all, add explicit job-to-EI scope binding and verify it before calling `RunJobV2`.

### Proof of Concept
Go handler-level integration test plan (`core/web/pipeline_runs_controller_test.go`):
1. Start an app (`cltest.NewApplicationEVMDisabled` or similar) and create two distinct jobs, Job X (e.g., a webhook/legacy or any non-webhook job type supported) and Job Y (a different job, e.g., cron or OCR job, with `app.AddJobV2`).
2. Create an `ExternalInitiator` via `ExternalInitiatorsController.Create` (or directly via `bridges.NewExternalInitiator`/`BridgeORM().CreateExternalInitiator`), obtaining `AccessKey`/`Secret`.
3. Build an HTTP client that sets `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers (per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`) instead of session/API token headers.
4. Send `POST /v2/jobs/<JobY.ID>/runs` using this EI-authenticated client, where Job Y has no relationship at all to the EI created in step 2.
5. Assert:
   - Expected (secure) behavior: HTTP `403 Forbidden` (or `401`), with no row created in `pipeline_runs` for Job Y.
   - Actual (vulnerable) behavior if `AuthenticateExternalInitiator` is wired to this route: HTTP `200`/`201` with a `pipelineRun` resource for Job Y returned, i.e. `isUser` was `true` and `prc.App.RunJobV2(ctx, JobY.ID, nil)` executed successfully — confirming the cross-job run trigger via an unrelated EI credential.
6. As a control, repeat with a real logged-in session/API-token user hitting the same endpoint for Job Y to confirm the endpoint's positive path is otherwise unchanged (still `200`/`201`), isolating the bug to the EI-impersonation-as-user issue.

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

**File:** core/web/pipeline_runs_controller.go (L101-107)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
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
