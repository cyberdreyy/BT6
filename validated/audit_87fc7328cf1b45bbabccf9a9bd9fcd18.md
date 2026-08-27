### Title
`AuthenticateExternalInitiator` grants a job-agnostic `UserRoleRun` session that is indistinguishable from a real user session, letting any EI credential trigger runs on **any** job by numeric ID via `POST /v2/jobs/:ID/runs` - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` stores a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` under the same `SessionUserKey` used by real session/token authentication. Downstream code (`PipelineRunsController.Create` in `core/web/pipeline_runs_controller.go`) relies on `auth.GetAuthenticatedUser(c)` returning `ok=true` to decide "is this a real user" (with a comment stating "EIs not allowed"), but since the EI-derived synthetic user is stored identically, this check is bypassed, allowing any authenticated EI to call `RunJobV2` on an arbitrary numeric job ID with no verification that the EI is bound to that job's webhook spec.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) authenticates the EI token against `FindExternalInitiator`/`bridges.AuthenticateExternalInitiator`, then unconditionally does: [1](#0-0) 
This sets `SessionUserKey` to a `*clsessions.User` with `Role: UserRoleRun` — the exact same context key and type used by `AuthenticateBySession`/`AuthenticateByToken` for real users: [2](#0-1) [3](#0-2) 

`GetAuthenticatedUser` just checks presence/type of that key, with no way to distinguish "real user" from "EI-synthesized user": [4](#0-3) 

The route `POST /v2/jobs/:ID/runs` is registered in the `userOrEI` group, which chains `AuthenticateExternalInitiator` before `AuthenticateByToken`/`AuthenticateBySession`, wrapped only by `RequiresRunRole`: [5](#0-4) 

`PipelineRunsController.Create` explicitly intends to restrict numeric-job-ID runs to real users only ("EIs not allowed"), gating on `isUser` from `GetAuthenticatedUser`: [6](#0-5) 

Because `GetAuthenticatedUser` cannot tell the EI-synthetic user apart from a real user, `isUser` is `true` for any successfully authenticated EI, so the "EIs not allowed" branch never actually excludes EIs. The handler then calls `prc.App.RunJobV2(ctx, jobID, nil)` for **any** numeric job ID supplied by the attacker — there is no lookup of `external_initiator_webhook_specs` to verify the authenticating EI is bound to that specific job. Thus an EI credential provisioned for job A's webhook can trigger a run of job B (any job in the node), not just its own bound job.

### Impact Explanation
This is an authorization/role-scoping bypass: a credential intended to be scoped to triggering runs of one specific webhook job can instead trigger pipeline runs for any job on the node by numeric ID, matching Chainlink's "unauthorized job run" bounty impact class. Depending on job type (e.g., jobs with side effects, on-chain writes gated behind flux monitor/keeper/other specs, or jobs performing external HTTP calls with attacker-influenced pipeline inputs), this can cause unintended job execution, resource exhaustion, or unauthorized on-chain transaction submission triggered by a party with no relationship to the target job.

### Likelihood Explanation
Any holder of a valid EI access key/secret pair (the lowest-privileged EI credential, obtainable via `POST /external_initiators` with `RequiresEditRole`, or already possessed by any legitimately provisioned external initiator) can exploit this with a single HTTP request — no additional preconditions, no admin access, fully repeatable, and requires only knowledge of a target numeric job ID (job IDs are small sequential integers, easily enumerable via `/v2/jobs` if any read access exists, or simply guessable).

### Recommendation
Distinguish EI-derived sessions from real user sessions explicitly (e.g., a separate context key or a `User.Role`/source flag that `PipelineRunsController.Create` and any other run-role handler can check), and additionally verify, before invoking `RunJobV2`, that the authenticated `ExternalInitiator` (from `GetAuthenticatedExternalInitiator`) is actually joined to the target job's `WebhookSpec` via `external_initiator_webhook_specs` before allowing the run.

### Proof of Concept
Go handler-level integration test plan:
1. Create two webhook jobs, Job A and Job B, each bound to a distinct External Initiator (EI-A and EI-B) via `cltest.CreateExternalInitiatorViaWeb` and job TOML with `externalInitiators = [...]`.
2. Also create a plain (non-webhook) job, Job C, with numeric ID, owned by no EI.
3. Authenticate as EI-A (headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret`).
4. Send `POST /v2/jobs/{JobC.ID}/runs` (numeric ID, not UUID) using EI-A's credentials.
5. Assert expected: `403`/`404` (EI-A has no relationship to Job C). Actual (bug): request succeeds (`200`) and a `pipeline_runs` row is created for Job C, because `isUser` in `PipelineRunsController.Create` evaluates true for the EI session and `RunJobV2` performs no EI-to-job binding check.
6. Repeat using EI-A's credentials against Job B's numeric ID to further confirm cross-job execution beyond EI-A's own bound job.

### Citations

**File:** core/web/auth/auth.go (L68-68)
```go
	c.Set(SessionUserKey, &user)
```

**File:** core/web/auth/auth.go (L109-109)
```go
	c.Set(SessionUserKey, &user)
```

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
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
