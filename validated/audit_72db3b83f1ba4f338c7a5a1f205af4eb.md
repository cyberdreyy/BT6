### Title
EI credential holder can trigger arbitrary job by int32 ID via unauthorized `isUser` check bypass - (File: core/web/pipeline_runs_controller.go)

### Summary
`PipelineRunsController.Create` intends to disallow external-initiator (EI) credentials from triggering jobs by numeric job ID ("only users are allowed to run jobs using int IDs - EIs not allowed"), but the check it uses (`auth.GetAuthenticatedUser`) is satisfied by EI-authenticated requests as well, because `auth.AuthenticateExternalInitiator` also injects a synthetic `SessionUserKey` user. This lets any authenticated EI credential holder call `prc.App.RunJobV2` with an arbitrary `int32` job ID with no verification that the target job has any `externalInitiators` binding to that EI at all.

### Finding Description
The route is registered as: [1](#0-0) 

using the `userOrEI` group, which chains `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, `auth.AuthenticateBySession` and wraps the handler with `auth.RequiresRunRole`.

`auth.AuthenticateExternalInitiator` validates the EI's access key/secret, then explicitly injects a fake authenticated *user* into the gin context in addition to the EI object: [2](#0-1) 

In `PipelineRunsController.Create`, the code comment states int32 job IDs should only be usable by "users", not EIs, and gates the numeric-ID path behind `isUser`: [3](#0-2) 

Because `auth.GetAuthenticatedUser(c)` merely checks for the presence of `SessionUserKey` in context, and `AuthenticateExternalInitiator` sets that same key to a synthetic `User{Role: UserRoleRun}`, `isUser` evaluates to `true` for EI-authenticated requests too — despite the comment's explicit intent to exclude EIs from this path. The code then calls `prc.App.RunJobV2(ctx, jobID, nil)` directly with the attacker-supplied `int32` ID, without any lookup or validation that the target job's `externalInitiators` spec includes the authenticated EI's name/credentials. `RunJobV2` performs no such binding check either — it simply looks up the job by ID and creates a run.

Thus request binding — the property that a gateway/EI request must be attributable to exactly one job that initiator is authorized for — is violated: an EI bound only to Job A can supply Job B's numeric ID and have it executed.

### Impact Explanation
This is an authorization bypass allowing unauthorized triggering of arbitrary jobs (Chainlink bounty class: unauthorized job run). An EI credential holder — a low-privilege, narrowly-scoped credential meant to trigger only its own bound webhook job(s) — can cause execution of any job in the node by numeric ID, potentially including jobs with side effects such as on-chain transactions, external HTTP calls, or other job types not intended to be externally triggerable at all by that initiator. This can result in resource exhaustion, unintended fund-moving transactions if a webhook/keeper-style job performs a transaction task, or interference with other users'/tenants' job pipelines on a shared node.

### Likelihood Explanation
Precondition: attacker must possess valid EI credentials (`X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret`) for at least one registered external initiator — no admin/edit privileges are required, and EI credentials are commonly distributed to external, less-trusted services. Given valid EI credentials, the exploit is a single, deterministic HTTP POST to `/v2/jobs/:anyJobID/runs` with the numeric job ID of any job on the node; no timing, race, or other conditions are needed. This is fully repeatable and requires no privileged access beyond a standard EI credential.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject the numeric-ID path when the request was authenticated via `AuthenticateExternalInitiator` (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` and bail out if present, rather than solely relying on the presence of a synthetic user), or alternatively remove the synthetic `SessionUserKey` injection from `AuthenticateExternalInitiator` and have EI-run authorization enforced solely via `SessionExternalInitiatorKey`, with `RunJobV2`/the controller validating that the job's `externalInitiators` binding matches the authenticated EI before executing.

### Proof of Concept
Handler-level integration test plan (Go, using `httptest` + app harness similar to existing tests, e.g. `core/services/job/runner_integration_test.go` patterns):
1. Start an app with `JobPipeline.ExternalInitiatorsEnabled = true`.
2. Create External Initiator `EI-A` via `cltest.CreateExternalInitiatorViaWeb`.
3. Create Job A (webhook job) with `externalInitiators = [{name = "EI-A", ...}]`, record `jobA.ID` (int32).
4. Create Job B (any job type — e.g. OCR or another webhook job not bound to EI-A), record `jobB.ID` (int32).
5. Using `EI-A`'s access key/secret headers (`X-Chainlink-EA-AccessKey`, `X-Chainlink-EA-Secret`), POST to `/v2/jobs/{jobB.ID}/runs`.
6. Assert expected secure behavior: HTTP 422/403 and `pipeline_runs` count unchanged for Job B (using `cltest.AssertCountStays`).
7. Actual observed behavior (per code trace): request succeeds with HTTP 200/201 and a pipeline run for Job B is created, confirming the bypass — `isUser` is true due to the synthetic user set by `AuthenticateExternalInitiator`, and `RunJobV2(ctx, jobB.ID, nil)` executes without checking `externalInitiators` binding to `EI-A`.

### Citations

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

**File:** core/web/auth/auth.go (L143-151)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
