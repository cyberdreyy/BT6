### Title
External Initiator authentication bypasses per-job binding checks on `POST /v2/jobs/:ID/runs`, allowing any authenticated EI to trigger arbitrary jobs by ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` gates job-run triggering on `auth.GetAuthenticatedUser(c)` returning a "user" with at least Run role, but `AuthenticateExternalInitiator` unconditionally injects a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` into the same context key used by real users. This means any valid External Initiator credential satisfies the `isUser` check and can call `RunJobV2` on any attacker-chosen integer job ID, with no verification that the specific job is associated with that EI.

### Finding Description
The route is registered under `RequiresRunRole(prc.Create)` and reached via either session/API-token auth or `auth.AuthenticateExternalInitiator`. In `core/web/auth/auth.go`, `AuthenticateExternalInitiator` (lines 119-151) validates the EI's access key/secret against the `external_initiators` table, but then does: [1](#0-0) 
setting `SessionUserKey` to a fabricated `User{Role: UserRoleRun}` regardless of which job (if any) the EI is bound to via `external_initiator_webhook_specs`.

`RequiresRunRole` only inspects this role field: [2](#0-1) 
so it passes for any successfully authenticated EI.

In `PipelineRunsController.Create`: [3](#0-2) 
the code comment claims "only users are allowed to run jobs using int IDs - EIs not allowed," but `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which returns `true` for EI-authenticated requests too, because `AuthenticateExternalInitiator` sets the same `SessionUserKey`. The handler then parses the URL `:ID` param directly as an `int32` job ID and calls `prc.App.RunJobV2(ctx, jobID, nil)` with **no lookup or comparison against the calling EI's bound job/webhook spec**. There is no code path in this function or in `RequiresRunRole` that cross-references `auth.GetAuthenticatedExternalInitiator(c)` against the target job's `external_initiator_webhook_specs` row.

### Impact Explanation
Any entity holding valid credentials for *any* registered External Initiator (even one never bound to any job) can trigger a pipeline run for *any* job ID in the node by simply guessing/enumerating small integer IDs — including jobs owned by other users/EIs, OCR/VRF/keeper jobs, etc. This enables cross-job run triggering, unauthorized resource consumption (running pipelines/consuming bridge credits, gas simulation, etc.), and violates the intended invariant that an EI credential is scoped to one authorized job. This maps to Chainlink's "unauthorized job run / authorization bypass" impact class.

### Likelihood Explanation
Preconditions: attacker needs valid credentials for *some* registered External Initiator (the lowest-privilege credential type this endpoint accepts) — no admin/edit/view-role user account is required. Job IDs are small sequential integers, trivially enumerable. The flaw is deterministic and repeatable on every request; no race condition or timing dependency is needed.

### Recommendation
In `PipelineRunsController.Create` (and any other `userOrEI` protected handler), when the caller is authenticated as an External Initiator (`auth.GetAuthenticatedExternalInitiator(c)`), look up the target job's `external_initiator_webhook_specs`/`WebhookSpec.ExternalInitiatorWebhookSpecs` and reject the request (403) unless the authenticated EI is bound to that specific job ID. Do not rely solely on the synthetic `UserRoleRun` injected for EI sessions to authorize job-scoped actions; require an explicit EI-to-job binding check before calling `RunJobV2`.

### Proof of Concept
1. Register two External Initiators, `EI-A` and `EI-B`, via `POST /v2/external_initiators`.
2. Create Job A (int32 ID `A_ID`) and Job B (int32 ID `B_ID`) as separate webhook-eligible jobs (or any job type reachable via `RunJobV2`).
3. Authenticate as `EI-A` using its access key/secret headers (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret`).
4. Send `POST /v2/jobs/{B_ID}/runs` using `EI-A`'s credentials.
5. Expected (secure) behavior: 401/403 because `EI-A` is not bound to Job B.
6. Actual (current) behavior: request succeeds with 200 and a `pipelineRun` resource is created for Job B, because `isUser` is satisfied by the injected `UserRoleRun` user and no EI-to-job binding check exists in `PipelineRunsController.Create`.
7. Table-driven Go test in `core/web/pipeline_runs_controller_test.go` asserting: `AuthenticateExternalInitiator` sets a run-role user for any valid EI regardless of job binding [1](#0-0) , and `PipelineRunsController.Create` calls `RunJobV2` without checking `GetAuthenticatedExternalInitiator` against the job's bound EI [4](#0-3) .

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

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
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

**File:** core/web/pipeline_runs_controller.go (L109-128)
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

	jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
}
```
