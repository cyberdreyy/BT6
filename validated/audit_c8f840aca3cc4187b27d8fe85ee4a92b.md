### Title
External-initiator credential holder can trigger a run on ANY job by integer ID, not just the job bound to their initiator - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` decides whether to allow a run purely by checking whether *any* user object exists in the gin context via `auth.GetAuthenticatedUser(c)`, but `AuthenticateExternalInitiator` unconditionally injects a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` into that same context key regardless of which job (if any) the initiator is registered against. The comment in the code claims "EIs not allowed" for integer-ID runs, but the implementation does not actually distinguish EI-authenticated requests from real user sessions, so an EI credential can trigger `RunJobV2` for an arbitrary job ID unrelated to the initiator.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered with three possible auth methods, tried in order: `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, `auth.AuthenticateBySession`, then wrapped with `auth.RequiresRunRole(prc.Create)`. [1](#0-0) 

`AuthenticateExternalInitiator` validates the EI AccessKey/Secret against the `bridges.ExternalInitiator` row, and on success sets `SessionExternalInitiatorKey` **and** unconditionally sets `SessionUserKey` to a synthetic run-role user — with no reference at all to any specific job: [2](#0-1) 

`RequiresRunRole` only checks that a user object exists in context and that its role isn't `View`; it has no notion of external initiators or job bindings: [3](#0-2) 

Inside `PipelineRunsController.Create`, the only "EI vs. user" distinction attempted is:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [4](#0-3) 

Because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `auth.GetAuthenticatedUser(c)` returns `ok == true` for EI-authenticated requests exactly as it does for token/session-authenticated requests. The `isUser` check therefore does not actually exclude EIs, contrary to the inline comment's stated intent. As a result, any valid EI credential reaches `prc.App.RunJobV2(ctx, jobID, nil)` for whatever integer job ID is supplied in the URL, with no lookup of which job(s) that specific `ExternalInitiator` row is associated with.

### Impact Explanation
This is an authorization/binding bypass: an external-initiator credential ("foo") that is unregistered against any job — or registered against a different job — can trigger `RunJobV2` for an arbitrary job by integer ID, matching the bounty class "unauthorized job/run triggering by an EI credential across jobs never bound to it." Depending on the job's pipeline, this could cause unintended fund movement, unwanted repeated on-chain transactions, or state corruption. This is scoped strictly to job-run triggering; it does not grant access to job specs, secrets, or admin/edit actions.

### Likelihood Explanation
Only a valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` pair for any registered `ExternalInitiator` is required — no job binding, no elevated role, and no other credential is needed. This makes the exploit trivially repeatable by anyone who has been issued (or otherwise obtains) EI credentials for unrelated purposes.

### Recommendation
`PipelineRunsController.Create` must reject any request authenticated via `AuthenticateExternalInitiator` and route such requests through a job-specific binding check (e.g., verifying that the target job has an `ExternalInitiatorWebhookSpec`/mapping tying it to `auth.GetAuthenticatedExternalInitiator(c)`), rather than relying on the generic `SessionUserKey` presence check. At minimum, `AuthenticateExternalInitiator` should not overwrite `SessionUserKey` with a role that satisfies `isUser` checks meant to gate user-only behavior, or the controller should check `auth.GetAuthenticatedExternalInitiator(c)` explicitly and reject/redirect EI-authenticated requests from the integer-ID path unless bound to that job.

### Proof of Concept
Go handler-level integration test plan (using existing test scaffolding in `core/web/pipeline_runs_controller_test.go` and `core/web/jobs_controller_test.go`):
1. Start a test app via `cltest.NewApplicationWithConfig`/`setupJobsControllerTests` helper as used elsewhere in the suite.
2. Create an `ExternalInitiator` named `foo` (via `bridges.ExternalInitiator` ORM or `POST /v2/external_initiators`) with no association to any job.
3. Create an unrelated job `jobIDofUnrelatedJob` (e.g., a simple OCR/keeper job) via the standard job-creation helper.
4. Send `POST /v2/jobs/{jobIDofUnrelatedJob}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `foo`'s credentials, and empty body.
5. Assert the response status. Per the stated invariant, a 4xx status is required; the current implementation returns `200 OK` with a `pipelineRun` resource body, and `App.JobORM()` shows a new run recorded against `jobIDofUnrelatedJob` — demonstrating the binding is not enforced.

### Citations

**File:** core/web/router.go (L449-456)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
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
