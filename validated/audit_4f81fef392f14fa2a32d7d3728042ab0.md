### Title
External Initiator authentication does not verify job binding, allowing any EI credential to trigger runs on webhook jobs owned by other initiators - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered in the `userOrEI` group with authentication methods `AuthenticateExternalInitiator`, `AuthenticateByToken`, `AuthenticateBySession`, then wrapped with `auth.RequiresRunRole(prc.Create)` [1](#0-0) .

`AuthenticateExternalInitiator` only verifies that the caller possesses a valid EI access key/secret pair (`store.FindExternalInitiator` + `bridges.AuthenticateExternalInitiator`), and on success it stores the identified `*bridges.ExternalInitiator` under `SessionExternalInitiatorKey`, but also unconditionally injects a synthetic `*clsessions.User{Role: clsessions.UserRoleRun}` under `SessionUserKey` [2](#0-1) .

In `PipelineRunsController.Create`, the handler calls `auth.GetAuthenticatedUser(c)` to decide whether the caller is a "user" allowed to run jobs by integer ID, and if so, directly calls `prc.App.RunJobV2(ctx, jobID, nil)` using only the `:ID` path parameter [3](#0-2) . Because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `isUser` evaluates to `true` for EI-authenticated requests as well — the code has no branch that distinguishes "real user session/token" from "EI-injected synthetic user." Consequently `RunJobV2` is invoked for *any* job ID supplied in the URL, with no lookup of `external_initiator_webhook_specs` and no comparison between the authenticated `ei.Name` (available via `auth.GetAuthenticatedExternalInitiator(c)`) and the target job's webhook spec's bound initiator name.

`GetAuthenticatedExternalInitiator` is defined in `core/web/auth/auth.go` but is never called anywhere else in the codebase, confirming that the identity of the external initiator is never cross-checked against the specific job's binding before triggering a run. The initiator-to-job binding (`external_initiator_webhook_specs`, see migration `core/store/migrate/migrations/0051_webhook_specs_external_initiators_join.sql`) is thus enforced nowhere in this authorization chain — the EI credential effectively behaves as a generic "run role" bearer token for every webhook job on the node, not just the job(s) it was provisioned for.

### Impact Explanation
This is an authorization-bypass vulnerability at the "unauthorized job run" impact class: a holder of any single external-initiator credential (which is meant to be scoped to specific webhook jobs) can trigger pipeline runs for arbitrary webhook jobs belonging to other initiators/tenants on the same node, potentially causing unauthorized on-chain transactions, resource exhaustion, or interference with unrelated jobs/consumers, without ever needing edit/admin credentials.

### Likelihood Explanation
The only precondition is possession of a valid (even minimally-scoped) external-initiator access key/secret pair for the target node — a credential class explicitly treated as low-trust ("run role only") in the codebase's own comment. The attack is a single HTTP POST with the attacker's own EI headers and an arbitrary integer job ID; no further guessing or race condition is needed if job IDs are discoverable/sequential, and the request is fully repeatable.

### Recommendation
In `PipelineRunsController.Create`, when authentication succeeded via `AuthenticateExternalInitiator` (i.e., `auth.GetAuthenticatedExternalInitiator(c)` returns ok), look up the job's `external_initiator_webhook_specs` binding and reject the run (401/422) unless the authenticated EI's name matches the job's bound initiator name. Alternatively, stop `AuthenticateExternalInitiator` from writing to `SessionUserKey` and give `PipelineRunsController.Create` a distinct code path for EI-authenticated requests that always performs the binding check before calling `RunJobV2`.

### Proof of Concept
Go handler-level integration test plan:
1. Create two external initiators, `EI-A` and `EI-B`, via `ExternalInitiatorsController.Create` (or ORM directly), obtaining separate access key/secret pairs.
2. Create a webhook job whose spec is bound only to `EI-A` in `external_initiator_webhook_specs` (via job spec TOML `externalInitiators: [EI-A]`).
3. Issue `POST /v2/jobs/{jobID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `EI-B`'s credentials.
4. Assert the response is currently `200 OK` with a created pipeline run (demonstrating the bypass), whereas the expected/fixed behavior should be `401 Unauthorized` or `422 Unprocessable Entity`.
5. Query `pipeline_runs` table for the job and assert a row was inserted despite `EI-B` not being bound to it — confirming the binding invariant violation; after the fix, assert zero rows are inserted for `EI-B`'s unauthorized attempt.

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
