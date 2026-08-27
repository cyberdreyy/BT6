### Title
External Initiator credentials are misidentified as user sessions, bypassing the "EIs not allowed" int-ID job run restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
`auth.AuthenticateExternalInitiator` stores a synthetic `*clsessions.User{Role: clsessions.UserRoleRun}` under the same `SessionUserKey` context key used for real user sessions/tokens. Consequently, `auth.GetAuthenticatedUser(c)` returns `ok=true` for external-initiator (EI) authenticated requests, causing the `isUser` check in `PipelineRunsController.Create` to incorrectly evaluate to `true` and let an EI credential holder trigger `RunJobV2` using an internal job ID, defeating the comment's explicit intent that "only users are allowed to run jobs using int IDs - EIs not allowed."

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered in a group that chains `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, `auth.AuthenticateBySession`, and then wraps the handler in `auth.RequiresRunRole(prc.Create)`: [1](#0-0) 

`AuthenticateExternalInitiator` authenticates the EI token and then explicitly sets both the EI object and a fabricated `User` object with `Role: UserRoleRun` under `SessionUserKey`: [2](#0-1) 

`GetAuthenticatedUser` simply reads whatever is stored at `SessionUserKey` and returns `ok=true` if present, without any way to distinguish a "real" user session/token from an EI-synthesized user: [3](#0-2) 

In `PipelineRunsController.Create`, the code relies on `isUser` from `GetAuthenticatedUser` to decide whether an int-ID job run is permitted, explicitly commenting that "EIs not allowed": [4](#0-3) 

Because `isUser` is `true` for EI-authenticated requests too, an EI credential holder that authenticates via `AuthenticateExternalInitiator` reaches `prc.App.RunJobV2(ctx, jobID, nil)` with an arbitrary integer job ID, rather than being rejected with "bad job ID" as the code comment intends. `RunJobV2` performs no check tying the job ID to the specific EI's registered webhook job, so any EI credential can trigger a run of any job by its internal numeric ID.

### Impact Explanation
This is an authorization-bypass / unauthorized-job-run issue: a lower-privileged credential type (external initiator, meant only to trigger its own associated webhook-derived runs) can run any job by internal ID, which the code explicitly intends to reserve for full user sessions/tokens. Depending on the jobs configured on the node (e.g., jobs performing on-chain transactions or fund movement via keeper/VRF/direct-request specs), this could let an EI holder trigger unintended job executions, corresponding to the "unauthorized job run" bounty impact class.

### Likelihood Explanation
The only precondition is possession of a valid external-initiator access key/secret (a credential explicitly designed to be lower-privileged than a full user session/API token). No admin, host, or database access is needed. The exploit is a single HTTP request: authenticate with EI headers and `POST /v2/jobs/{intID}/runs`. It is fully repeatable and deterministic given the code path shown above.

### Recommendation
Distinguish EI-derived pseudo-user contexts from real user auth, e.g., store EI authentication under a different marker (or check `GetAuthenticatedExternalInitiator` first and reject/short-circuit before checking `isUser`), so `Create` can positively confirm no external initiator is set before allowing int-ID `RunJobV2` execution. Alternatively, have `AuthenticateExternalInitiator` not populate `SessionUserKey` at all, and have `RequiresRunRole` accept either a real user with run-role or a validated EI object explicitly, without conflating the two under `GetAuthenticatedUser`.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a test app with an `ExternalInitiator` registered (valid `AccessKey`/`Secret`) and a job created with a known int `jobID`.
2. Build a request `POST /v2/jobs/{jobID}/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`) matching the registered EI, and no session cookie or API token headers.
3. Send the request through the router (`v2Routes` / `NewRouter`).
4. Assert (current buggy behavior) that the response is `200 OK` with a pipeline run resource — i.e., `RunJobV2` was invoked — instead of the expected `422 Unprocessable Entity` with error `"bad job ID"`.
5. Add a unit test directly on `auth.GetAuthenticatedUser` after calling `auth.AuthenticateExternalInitiator(c, authr)`, asserting that `isUser` should be `false` (currently returns `true`), demonstrating the root cause.

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

**File:** core/web/auth/auth.go (L177-187)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
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
