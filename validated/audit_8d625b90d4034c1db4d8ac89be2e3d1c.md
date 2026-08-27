### Title
External Initiator credential can trigger arbitrary job runs for any int32 job ID, not just its own webhook job - ([File: core/web/pipeline_runs_controller.go])

### Summary
The `POST /v2/jobs/:ID/runs` route is reachable by External Initiator (EI) credentials via the `userOrEI` group in `core/web/router.go`, and `AuthenticateExternalInitiator` synthesizes a `*clsessions.User{Role: clsessions.UserRoleRun}` that satisfies both `auth.RequiresRunRole` and the `isUser` check inside `PipelineRunsController.Create`. Because the int32 branch of `Create` calls `prc.App.RunJobV2(ctx, jobID, nil)` using only the URL path ID with no binding back to the specific EI's own job/webhook spec, any valid EI credential can trigger a run of an arbitrary job by numeric ID, bypassing the intended "EIs not allowed to run int-ID jobs" restriction stated in the code comment.

### Finding Description
The route is registered as: [1](#0-0) 

`userOrEI` accepts `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession` — any one of them succeeding grants access. `AuthenticateExternalInitiator` verifies EI access-key/secret against the DB-stored `bridges.ExternalInitiator`, then sets: [2](#0-1) 

This synthetic user has `Role: clsessions.UserRoleRun`, which passes `RequiresRunRole` (only `UserRoleView` is rejected): [3](#0-2) 

Inside `PipelineRunsController.Create`, the UUID/webhook branch now always errors out with `job.ErrJobTypeRemoved`, so control falls through to the int32 branch, gated by `isUser`: [4](#0-3) 

The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which only checks that `SessionUserKey` is set to a `*clsessions.User` — a condition the EI's synthetic user object also satisfies. There is no check for `SessionExternalInitiatorKey` being absent, and no verification that the numeric `jobID` corresponds to a job spec owned by/associated with the authenticated EI. Consequently, an attacker holding valid credentials for *any* registered EI can supply an arbitrary `int32` job ID belonging to a completely unrelated job (e.g., another team's webhook or even a non-webhook job type) and have `RunJobV2` execute it.

### Impact Explanation
This is an authorization bypass allowing a low-privilege External-Initiator credential holder to trigger pipeline runs of jobs they do not own or aren't bound to, potentially causing unauthorized on-chain transactions, unwanted external HTTP/bridge calls, resource exhaustion, or triggering of jobs with side effects (e.g., writing transactions, VRF fulfillment attempts) outside their intended scope. This falls under "unauthorized job run" impact class in the Chainlink node bounty scope.

### Likelihood Explanation
Exploitation only requires possession of one valid EI access-key/secret pair (a low-privilege credential type explicitly designed to be restricted to triggering its own webhook job). No admin/session/API-token access is needed. The attack is a single unauthenticated-by-role HTTP POST with a guessed/enumerated small int32 job ID, fully repeatable and scriptable.

### Recommendation
In `PipelineRunsController.Create`, explicitly deny EI-authenticated requests from the int32 path — check `auth.GetAuthenticatedExternalInitiator(c)` and reject (401/403) if present, rather than relying solely on `isUser` (which is also true for EIs due to the synthetic user object). Alternatively, since the comment's original intent was for EIs to only reach jobs via their bound webhook spec (now removed), consider removing EI access to this route entirely, since UUID-based webhook triggering (`job.Webhook`) has already been removed (`job.ErrJobTypeRemoved`).

### Proof of Concept
1. Set up an integration test with two jobs: Job A owned by/associated with a registered External Initiator "ei1", and Job B (any other job type, unrelated to "ei1").
2. Register `ei1` with valid access key/secret via `ExternalInitiatorsController` or fixture, obtaining `EI-ACCESSKEY`/`EI-SECRET` headers.
3. `POST /v2/jobs/<JobB-int-ID>/runs` with only `EI-ACCESSKEY`/`EI-SECRET` headers set (no session cookie, no API token).
4. Assert current (vulnerable) behavior: HTTP 200/201 with a `pipelineRun` resource for Job B, proving `RunJobV2` executed for a job unrelated to the EI's credential.
5. After fix: assert HTTP 401/403 and that `App.RunJobV2` was never invoked for Job B under EI-only auth.

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
