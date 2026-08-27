### Title
External Initiator credentials can trigger arbitrary job runs via `/v2/jobs/:ID/runs` despite the "EIs not allowed" restriction being effectively bypassed - ([File: core/web/pipeline_runs_controller.go])

### Summary
The route `userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))` in `core/web/router.go` is reachable by holders of `AuthenticateExternalInitiator` credentials as well as session/token users. `PipelineRunsController.Create` attempts to restrict integer-ID job runs to real users only via `isUser := auth.GetAuthenticatedUser(c)`, but this check is defeated because `AuthenticateExternalInitiator` also populates the `SessionUserKey` context value, making `isUser` true for EI-authenticated requests too.

### Finding Description
In `core/web/router.go`, the `userOrEI` route group authenticates via `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, or `auth.AuthenticateBySession` [1](#0-0) , and only wraps the handler with `auth.RequiresRunRole`, which merely rejects `UserRoleView` [2](#0-1) .

In `AuthenticateExternalInitiator`, after validating the EI access key/secret, the code sets both `SessionExternalInitiatorKey` and `SessionUserKey` to a synthetic `User{Role: clsessions.UserRoleRun}`: [3](#0-2) 

In `PipelineRunsController.Create`, the code comment explicitly states "only users are allowed to run jobs using int IDs - EIs not allowed" and gates the numeric-ID run path on `isUser`: [4](#0-3) 

Because `auth.GetAuthenticatedUser(c)` simply reads whatever was placed at `SessionUserKey` [5](#0-4) , and `AuthenticateExternalInitiator` always injects a fake `User` object there, `isUser` evaluates to `true` for EI-authenticated requests exactly the same as for real session/token users. The intended EI exclusion never actually triggers — any request that successfully authenticates via EI credentials falls into the same `isUser == true` branch and can call `prc.App.RunJobV2(ctx, jobID, nil)` for **any** integer job ID on the node, not just a job tied to that external initiator's webhook.

### Impact Explanation
This is an authorization/role-confusion bug: an external-initiator credential (intended only to be able to trigger its own associated webhook-style job runs) can instead trigger a pipeline run for **any** job on the node by supplying an arbitrary integer job ID, with no ownership/association check between the EI and the target job. This matches the "unauthorized job run" bounty impact class — a lower-privileged, narrowly-scoped credential (EI key/secret) can invoke `RunJobV2` for jobs it has no legitimate relationship to, potentially triggering unintended fund-moving or state-changing pipeline runs (e.g., OCR/keeper/VRF-adjacent jobs, transaction-emitting tasks) for jobs unrelated to that EI.

### Likelihood Explanation
Minimal precondition: possession of any valid External Initiator access key/secret pair (a common, restricted credential type distributed to third-party initiator integrations). No admin/session/API-token access needed. The attack is a single, repeatable HTTP `POST /v2/jobs/:ID/runs` request with `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers and an integer job ID; feasibility is high since the flawed check is on every request.

### Recommendation
Distinguish EI-authenticated requests from real user sessions explicitly, rather than relying on `SessionUserKey` presence. For example, check `auth.GetAuthenticatedExternalInitiator(c)` and reject/limit that path separately, or set a distinct context flag when authenticating as an EI so `PipelineRunsController.Create` can reliably branch on true user vs EI identity, restoring the documented intent that "EIs not allowed" for integer-ID job runs (or, if EIs are meant to be allowed, add an explicit authorization check binding the EI to the specific job ID before calling `RunJobV2`).

### Proof of Concept
Handler-level Go test plan:
1. Set up a test `gin.Engine` with `v2Routes` registered against a mocked `chainlink.Application` whose `AuthenticationProvider().FindExternalInitiator` returns a valid `ExternalInitiator` for supplied EI credentials, and whose `RunJobV2` is mocked to record calls.
2. Create a job with integer ID `N` that has no relation to the external initiator being used.
3. Send `POST /v2/jobs/N/runs` with only `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set (no session cookie, no API token).
4. Assert the handler does not return `422 Unprocessable Entity` ("bad job ID") and instead calls `prc.App.RunJobV2(ctx, N, nil)`, returning `200`/pipeline-run JSON — demonstrating that despite the "EIs not allowed" comment, the EI successfully triggered a run for an arbitrary job ID.
5. Contrast with expected behavior: assert the request should have been rejected (e.g., `422`) to confirm the check bypass.

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

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
