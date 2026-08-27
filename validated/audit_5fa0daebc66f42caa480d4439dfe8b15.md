Looking at `PipelineRunsController.Create`, the code path for external initiators has been effectively removed. The webhook job type (which was the only job type driven by external-initiator-triggered runs bound to a specific `externalInitiators` name) is now rejected outright.### Title
External Initiator credentials satisfy `isUser` check and can trigger arbitrary jobs by integer ID via `POST /v2/jobs/:ID/runs`, bypassing intended EI-to-job binding - ([File: core/web/pipeline_runs_controller.go])

### Summary
The specific attack described in the question (reusing one EI's AccessKey/Secret against a UUID-keyed webhook job "belonging" to another EI) is not exploitable, because that entire code path always returns `422 Unprocessable Entity` before any authorization/binding check runs, since the webhook job type has been removed. However, a related and more severe binding-bypass exists on the integer-job-ID branch of the same handler: `auth.AuthenticateExternalInitiator` sets the same `SessionUserKey` used by normal user sessions/tokens, so any valid External Initiator credential (its own, or another EI's) is treated as `isUser == true` in `PipelineRunsController.Create` and can trigger `RunJobV2` for any job ID, completely bypassing the intended restriction that "EIs not allowed" to run jobs by integer ID.

### Finding Description
The reachable route is registered in `core/web/router.go`: [1](#0-0) 
Both external initiators and regular users are authenticated by the same `userOrEI` middleware chain, with `auth.AuthenticateExternalInitiator` tried first.

Inside `AuthenticateExternalInitiator`, a successful EI auth (any valid EI, not bound to any specific job) sets `SessionUserKey` to a generic `Role: UserRoleRun` user object, identical to what a normal run-role user session would produce: [2](#0-1) 

`PipelineRunsController.Create` then branches on the job ID format: [3](#0-2) 

- If `idStr` parses as a UUID (the historical webhook-job-ID format used with `externalInitiators` binding), the handler unconditionally returns 422 with `job.ErrJobTypeRemoved` — this closes off the exact scenario in the question (webhook job bound to EI-B, triggered with EI-A's headers), because the request never reaches any binding check; it fails identically for correct or incorrect EI credentials, and no run is created either way.
- If `idStr` parses as an int32, the code calls `auth.GetAuthenticatedUser(c)`, which merely checks for the presence of `SessionUserKey`, not its origin. Because `AuthenticateExternalInitiator` sets this same key, `isUser` is `true` for *any* successfully authenticated External Initiator. The handler then calls `prc.App.RunJobV2(ctx, jobID, nil)` directly for the given integer job ID with **no check whatsoever** relating the job to the authenticating EI's name/identity — there is no per-job "bound initiator" concept left in this code path.

The inline comment "only users are allowed to run jobs using int IDs - EIs not allowed" documents the intended control, but the implementation fails to enforce it due to the shared session-key representation between user and EI authentication.

### Impact Explanation
Any holder of a valid External Initiator AccessKey/Secret pair (obtainable by any unprivileged actor who can call `POST /v2/external_initiators` with edit role, or any leaked/compromised EI credential) can trigger a pipeline run for **any** job addressable by its integer ID — not limited to jobs associated with that EI — via `POST /v2/jobs/:ID/runs`. This is an authorization/role-confusion bug enabling unauthorized job execution, matching the "unauthorized job run" bounty impact class. It is broader than the specific cross-EI-header-reuse scenario asked about, since it doesn't even require knowledge of another EI's job binding — any EI credential works for any job.

### Likelihood Explanation
- Requires only a valid EI AccessKey/Secret pair, which is one of the explicitly allowed unprivileged attacker capabilities in this audit.
- No additional secrets, admin access, or misconfiguration is needed — this is a logic flaw in `PipelineRunsController.Create` combined with `auth.AuthenticateExternalInitiator`'s session-key handling.
- Fully repeatable and deterministic: any EI credential + any integer job ID reproduces the issue.
- The originally hypothesized webhook/UUID cross-EI attack is **not** exploitable, since `job.ErrJobTypeRemoved` unconditionally short-circuits it for all callers regardless of credentials.

### Recommendation
Distinguish EI-authenticated sessions from real user sessions at the point of use rather than relying on a shared `SessionUserKey`/`Role` value. In `PipelineRunsController.Create`, explicitly check for the presence of `SessionExternalInitiatorKey` (or an equivalent EI marker) and reject (401/403) integer-ID job-run requests made via EI authentication, per the existing code comment's intent. If EI-triggered runs by integer job ID are meant to be supported at all, they must validate that the authenticated EI is actually bound/authorized for that specific job before calling `RunJobV2`.

### Proof of Concept
Handler-level integration test plan (Go):
1. Start an application with `JobPipeline.ExternalInitiatorsEnabled = true`.
2. Create External Initiator EI-A via `POST /v2/external_initiators` (as edit-role user), capture `AccessKey`/`Secret`.
3. Create a normal (non-webhook) job (e.g., an OCR/keeper/cron job) with integer ID `jobID` via an authenticated user, unrelated to EI-A.
4. Send `POST /v2/jobs/<jobID>/runs` using `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set to EI-A's credentials (no session/token).
5. Assert current (vulnerable) behavior: response is `200 OK` and a new row appears in `pipeline_runs` for `jobID`, despite EI-A having no relationship to that job — i.e., `cltest.AssertServerResponse(t, resp, http.StatusOK)` and run-count increases.
6. Additionally verify the webhook/UUID scenario from the question is *not* exploitable: `POST /v2/jobs/<jobUUID>/runs` with EI-B's job UUID and EI-A's headers returns `422` and `cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", 0)`, matching `TestRunner_WebhookJobRemoved`.
7. After applying the recommended fix, repeat step 4 and assert `401`/`403` and no new `pipeline_runs` row.

### Citations

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
}
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

**File:** core/web/pipeline_runs_controller.go (L101-125)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}

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
