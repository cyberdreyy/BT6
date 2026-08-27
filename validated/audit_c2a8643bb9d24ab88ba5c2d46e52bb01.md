### Title
External Initiator credentials can trigger runs of *any* job via `/v2/jobs/:ID/runs`, bypassing the intended "EIs not allowed" restriction — ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` grants every successfully authenticated External Initiator (EI) request a `clsessions.User{Role: clsessions.UserRoleRun}` stored under the same `SessionUserKey` used for real users. `PipelineRunsController.Create` attempts to block EIs from running jobs by integer ID (`isUser := auth.GetAuthenticatedUser(c)`), but that check only tests for presence of `SessionUserKey`, which is also set for EI requests, so the restriction never actually excludes EIs. Combined with the route `userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))` accepting both EI and User auth, any valid EI credential can trigger a run of any job by integer ID, with no scoping to the job(s) the EI was provisioned for.

### Finding Description
- `AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) authenticates EI access-key/secret against `FindExternalInitiator`/`bridges.AuthenticateExternalInitiator`, then does: [1](#0-0) 
setting both `SessionExternalInitiatorKey` and `SessionUserKey` (the latter with `UserRoleRun`), with no binding of the granted role to a specific job or job name.
- The route table wires a single group for both real users and EIs: [2](#0-1) 
`POST /v2/jobs/:ID/runs` is gated only by `auth.RequiresRunRole`, which merely checks `user.Role != UserRoleView` (`core/web/auth/auth.go:200-217`) — it has no concept of "this EI may only run job X".
- `PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-127`) tries to compensate with a comment "only users are allowed to run jobs using int IDs - EIs not allowed" and a runtime check: [3](#0-2) 
`auth.GetAuthenticatedUser(c)` (`core/web/auth/auth.go:178-187`) only checks whether `SessionUserKey` is present and type-asserts to `*clsessions.User` — it cannot distinguish a session/token-authenticated user from an EI-authenticated request, because `AuthenticateExternalInitiator` sets the identical key/type. Therefore `isUser` evaluates to `true` for EI-authenticated calls too, and the intended EI exclusion is dead code.
- The only real job/EI binding that ever existed was the now-removed `Webhook` job type's `external_initiator_webhook_specs` join table (`core/services/job/models.go:462-475`, migration `0051_webhook_specs_external_initiators_join.sql`). Webhook job creation/execution is now rejected outright ("job type webhook has been removed"), confirmed by `TestPipelineRunsController_CreateWebhookJobRejected` and `TestPipelineRunsController_RunExistingWebhookJobRejected` (`core/web/pipeline_runs_controller_test.go:34-72`). So the one mechanism that scoped an EI to a job no longer functions, while the broken `isUser` bypass still lets EI credentials reach `RunJobV2` for arbitrary integer job IDs of any job type (OCR, direct request, cron, etc.), not just jobs it was ever associated with.

### Impact Explanation
Any holder of a valid EI access-key/secret pair (a low-privilege, narrowly-scoped credential type intended only to trigger its own webhook job) can invoke `RunJobV2` for any job on the node identified by integer ID, causing unauthorized job execution across unrelated jobs/pipelines. Depending on job task graphs, this can cause unintended on-chain transactions, resource exhaustion, or unauthorized use of bridges/external adapters tied to other jobs — this maps to Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
Requires only a single valid EI access-key/secret (any EI created via `/v2/external_initiators`), which is a purposely low-trust credential distributed to third-party initiators. No admin/edit/view-role account needed. The exploit is a single unauthenticated-role HTTP POST once the EI credential is known, is fully repeatable, and requires no race condition or timing dependency.

### Recommendation
Do not overload `SessionUserKey` for EI-authenticated requests. Use a distinct marker (rely solely on `SessionExternalInitiatorKey`) so `PipelineRunsController.Create`'s `isUser` check (and any future role checks) can correctly discriminate EI-originated requests from real user sessions/tokens, and reject non-UUID/int job-run requests from EI callers entirely (or reinstate per-EI job binding enforcement if EI-triggered runs are still a supported feature).

### Proof of Concept
Go handler-integration test in `core/web/pipeline_runs_controller_test.go`:
1. Start an app, create a non-webhook job (e.g. OCR spec as in `setupPipelineRunsControllerTests`), capture its int32 `jb.ID`.
2. Create an External Initiator `foo` via ORM/`bridges.NewORM`, obtaining `AccessKey`/plaintext secret.
3. Build an HTTP client that sets `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`) instead of session/API-token headers.
4. POST `/v2/jobs/<jb.ID>/runs` with this EI client.
5. Assert response is `200 OK` with a created pipeline run (not `401 Unauthorized`/`422 bad job ID`), proving the EI credential — never associated with this job — successfully triggered an unrelated job's run, confirming the missing per-EI job scoping and the broken `isUser` exclusion.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
```

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
