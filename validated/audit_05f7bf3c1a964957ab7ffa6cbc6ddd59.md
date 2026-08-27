### Title
Cross-job triggering via unscoped `run` role granted to any External Initiator credential - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` unconditionally sets the session user to `&clsessions.User{Role: clsessions.UserRoleRun}` for any successfully authenticated external initiator (EI), with no binding of the resulting "user" to the specific job(s) that EI was provisioned for. Because `PipelineRunsController.Create` (and any other `run`-role-gated handler) only checks `auth.GetAuthenticatedUser(c)` for "is this a user" via `isUser`, an EI credential is indistinguishable from a full run-role user and can trigger `POST /v2/jobs/:ID/runs` for any job ID on the node, not just the job it was created/scoped for.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) looks up the EI purely by access key/secret via `store.FindExternalInitiator` and, on success, does: [1](#0-0) 
This sets `SessionUserKey` to a generic `run`-role `User` object with no reference to the `ExternalInitiator`'s `Name`/`ID`/associated job. The `SessionExternalInitiatorKey` is also set, but downstream `run`-gated handlers do not consult it to restrict which job(s) the request may act on.

`RequiresRunRole` (`core/web/auth/auth.go:202-217`) only checks `user.Role != clsessions.UserRoleView`, which the EI's synthetic user passes trivially.

Critically, `PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-128`) determines eligibility to run a job by integer ID using: [2](#0-1) 
The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but `auth.GetAuthenticatedUser(c)` returns `ok=true` for EI-authenticated requests too, since `AuthenticateExternalInitiator` populates the exact same `SessionUserKey` used for real users. There is no code path distinguishing "real dashboard/API user" from "EI masquerading as a run-role user," and no check against `job.ExternalJobID`/job's `externalInitiators` webhook spec bound to the authenticated EI's name. As a result, any EI credential can call `POST /v2/jobs/:jobID/runs` with an arbitrary integer job ID belonging to any job on the node — not just the job it was configured for.

### Impact Explanation
An attacker who legitimately holds a valid low-privilege EI access-key/secret pair (e.g., provisioned for job "foo") can trigger job runs of unrelated jobs configured with numeric IDs on the same node, bypassing the intended per-EI job scoping. This maps to Chainlink's "unauthorized job run" impact class — an unprivileged, narrowly-scoped credential holder gains the ability to invoke node execution paths (and any side effects those pipelines have, e.g. on-chain transactions, external HTTP calls, secrets usage) for jobs outside its intended authorization boundary.

### Likelihood Explanation
Exploitation requires only a single valid EI access-key/secret pair issued for any job/external initiator — no admin or elevated credentials needed. The request is a straightforward, unauthenticated-boundary-crossing HTTP `POST` to `/v2/jobs/:ID/runs` using standard EI headers (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret`). It is fully repeatable and requires no race conditions or timing — a deterministic logic flaw.

### Recommendation
In `AuthenticateExternalInitiator`, do not grant a full run-role "user" object indistinguishable from real users; instead attach the `ExternalInitiator` identity distinctly (already done via `SessionExternalInitiatorKey`) and require any run-triggering handler to explicitly check `auth.GetAuthenticatedExternalInitiator(c)` before permitting EI-driven requests, validating that the target job's associated `ExternalInitiator`/webhook spec matches the authenticated EI's `Name`/`ID`. In `PipelineRunsController.Create`, replace the `isUser` boolean derived from `GetAuthenticatedUser` with an explicit differentiation: reject requests originating from `GetAuthenticatedExternalInitiator` unless the target job's spec is bound to that specific initiator.

### Proof of Concept
Go handler-level integration test plan:
1. Create two webhook-capable jobs, `jobA` (int ID `1`) and `jobB` (int ID `2`), each with distinct `externalInitiators` TOML specs bound to EI `foo` and EI `bar` respectively, via `app.BridgeORM().CreateExternalInitiator` and job creation helpers (see `core/web/router_test.go` patterns using `bridges.NewExternalInitiator`).
2. Build the router with `web.Router(t, app, nil)` and start an `httptest.Server`.
3. Send `POST /v2/jobs/2/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI `foo`'s credentials (scoped only to `jobA`).
4. Assert current (vulnerable) behavior: `http.StatusOK`/`201` and a `pipelineRun` resource is returned for `jobB`, proving `foo`'s credentials triggered `jobB`.
5. Expected fixed behavior: response should be `http.StatusUnauthorized`/`403` because EI `foo` is not scoped to `jobB`.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
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
