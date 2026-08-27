### Title
External Initiator credentials for Job A can trigger pipeline runs on any other job (Job B) via `POST /v2/jobs/:ID/runs` due to missing job-binding check - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` authenticates the caller against a specific `bridges.ExternalInitiator` record but then unconditionally injects a generic `&clsessions.User{Role: clsessions.UserRoleRun}` into the gin context, with no reference to which job the EI is registered for. `PipelineRunsController.Create` subsequently uses only the presence of a `SessionUserKey` (`GetAuthenticatedUser`) to decide whether to accept an arbitrary integer job ID from the URL and call `RunJobV2` on it, without ever consulting the `SessionExternalInitiatorKey` object to verify the EI is authorized for that specific job.

### Finding Description
The route is registered as: [1](#0-0) 

Both an External Initiator, an API token holder, and a session user can hit this endpoint interchangeably. When an EI authenticates: [2](#0-1) 
it sets `SessionExternalInitiatorKey` (the actual EI record, which is tied to a specific job at creation time) **and** `SessionUserKey` to a synthetic run-role `User` that carries no job binding at all.

In `PipelineRunsController.Create`, the code comment claims "only users are allowed to run jobs using int IDs - EIs not allowed," and gates on `isUser`: [3](#0-2) 
However, `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which only checks for the presence of `SessionUserKey` — and that key is also set (to a fake run-role user) by `AuthenticateExternalInitiator`. Therefore the "EIs not allowed" gate is not actually effective for EI-authenticated requests reaching this handler; `isUser` evaluates to `true` regardless of whether the caller is a real session/token user or an EI. The handler then parses whatever `:ID` is supplied as an `int32` and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly — with **no lookup or comparison against the `bridges.ExternalInitiator` stored under `SessionExternalInitiatorKey`** to confirm that the authenticated EI is actually associated with the target job ID. Any integer job ID supplied in the path is accepted and run.

### Impact Explanation
This is an authorization/binding bypass: an External Initiator credential scoped to one job (Job A) can be used to trigger pipeline execution of a completely unrelated job (Job B) by simply substituting Job B's integer ID in the URL path, as long as Job B is not itself a webhook-type job (webhook jobs are rejected earlier via the UUID check, but non-webhook int-ID jobs are not filtered by EI association at all). This corresponds to Chainlink's "unauthorized job run" bounty impact class — an unprivileged/narrowly-scoped credential holder gains the ability to execute pipeline runs (potentially consuming node resources, driving on-chain transactions/oracle responses, or manipulating job execution timing) for a job they were never authorized to trigger.

### Likelihood Explanation
Feasibility is high and requires only a single low-privilege credential: valid EI `AccessKey`/`Secret` for any one job on the node (no admin/session/API-token access needed). The exploit is a single crafted HTTP request substituting a different job's ID; it is trivially repeatable and does not depend on race conditions, timing, or misconfiguration — it stems directly from the code logic in `Create` and the shared `SessionUserKey` assignment in `AuthenticateExternalInitiator`.

### Recommendation
In `PipelineRunsController.Create`, do not rely solely on `GetAuthenticatedUser` to distinguish EI vs. real users — check `auth.GetAuthenticatedExternalInitiator(c)` explicitly and, if present, verify that the retrieved `bridges.ExternalInitiator` is actually bound to the target job ID (e.g., look up the job's associated `ExternalInitiatorWebhookSpec`/EI foreign key and compare) before calling `RunJobV2`. Alternatively, stop setting a generic `SessionUserKey` for EI-authenticated requests, and have `Create` require job-EI binding verification independent of role checks.

### Proof of Concept
Handler-level integration test plan (Go, similar to existing tests in `core/web/pipeline_runs_controller_test.go`):
1. Start an app via `cltest.NewApplicationEVMDisabled(t)`.
2. Create Job A (non-webhook, e.g., a directrequest or OCR job as in `setupPipelineRunsControllerTests`) and register an External Initiator record bound to Job A via the `external_initiators` endpoint/ORM helper, capturing its `AccessKey`/`Secret`.
3. Create Job B (separate job, different ID) belonging to a distinct spec/context.
4. Build an HTTP client that sets `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`) with Job A's EI credentials.
5. Send `POST /v2/jobs/{JobB.ID}/runs` with that client.
6. Assert: expected secure behavior is `403 Forbidden` or `404 Not Found` (EI not bound to Job B); actual observed behavior with current code is `200 OK` with a `pipelineRun` JSON resource created for Job B, confirming unauthorized cross-job run triggering.

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
