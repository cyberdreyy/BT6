### Title
EI-authenticated requests can trigger job runs by integer job ID, bypassing the "EIs not allowed" restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
The route `POST /v2/jobs/:ID/runs` is registered under a middleware chain that includes `auth.AuthenticateExternalInitiator`, which sets `SessionUserKey` to a synthetic `clsessions.User{Role: UserRoleRun}` for any valid EI credential holder. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the caller is a "user" (as opposed to an EI) allowed to run jobs by integer ID, but that check only inspects the presence/type of the value at `SessionUserKey`, which is indistinguishable between a real session/token user and an EI-derived synthetic user. This lets any EI-credential holder invoke `RunJobV2` for arbitrary integer job IDs, contradicting the code's explicit intent.

### Finding Description
The route is registered in `core/web/router.go` as: [1](#0-0) 

The `userOrEI` group applies `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, and `auth.AuthenticateBySession` in sequence (first success wins) and then wraps the handler with `auth.RequiresRunRole`.

In `AuthenticateExternalInitiator`, upon successful EI credential verification, the code explicitly sets the same context key used for real users: [2](#0-1) 

`RequiresRunRole` only checks `user.Role != UserRoleView`, so the synthetic `UserRoleRun` user passes it: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code retrieves the "authenticated user" and treats a truthy `isUser` as permission to run an arbitrary integer job ID, explicitly documenting that EIs should not be allowed to do this: [4](#0-3) 

Because `GetAuthenticatedUser` merely does a type assertion on the context value at `SessionUserKey` — which `AuthenticateExternalInitiator` populates with a `*clsessions.User` indistinguishable from a genuine session/token-authenticated user — `isUser` evaluates to `true` for EI-authenticated callers too: [5](#0-4) 

There is no check that ties the job ID to the specific external initiator's associated job (e.g., via `bridges.ExternalInitiator` webhook-spec binding); any integer ID is accepted and passed straight to `prc.App.RunJobV2(ctx, jobID, nil)`. This breaks the intended invariant that an EI can only trigger the job(s) it was provisioned for (originally enforced via UUID-bound webhook job specs), because the comment's assumption ("only users are allowed to run jobs using int IDs - EIs not allowed") is not actually enforced by any code — it relies on `isUser` being false for EIs, but it is not.

### Impact Explanation
An attacker holding valid credentials for any single external initiator (a low-privilege, narrowly-scoped credential intended to trigger only its own bound job) can trigger `RunJobV2` for any job in the node identified by an arbitrary integer ID — including jobs unrelated to that initiator (e.g., other tenants'/users' OCR, direct request, cron, or flux monitor jobs, depending on what accepts direct runs). This is an authorization/role bypass leading to unauthorized job-run triggering across job boundaries, matching the "unauthorized job run" impact class. The severity depends on the downstream effects of the specific job triggered (e.g., could initiate transactions or external side effects), but as a general primitive, it is a REQUEST_BINDING violation: the request is not confined to the authorized initiator's own job.

### Likelihood Explanation
Precondition is minimal: only valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-AccessSecret` (per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`) for any one existing external initiator on the node — no admin, no edit role, no knowledge of the target job's owner. The attack is a single POST request and fully repeatable for any integer job ID the attacker wishes to guess or enumerate. This is highly feasible given EI credentials are commonly distributed to third-party/external systems with the expectation they can only trigger their own bound job.

### Recommendation
In `PipelineRunsController.Create`, distinguish EI-derived synthetic users from real session/token users before permitting integer-ID job runs. Concretely: check `auth.GetAuthenticatedExternalInitiator(c)` and reject (or require explicit job-to-initiator binding) if an EI is present, rather than solely checking `GetAuthenticatedUser`. Alternatively, tag the synthetic EI user with a distinguishable marker (not reusing `SessionUserKey` for both, or adding an explicit `IsExternalInitiator` field) so `isUser` in `Create` correctly evaluates to `false` for EI-authenticated requests, restoring the documented "EIs not allowed" intent.

### Proof of Concept
Go handler-level integration test plan (in `core/web/pipeline_runs_controller_test.go` or similar):
1. Set up a `gin.Engine` with the `userOrEI` route group exactly as in `v2Routes` (or invoke `NewRouter` with a test `chainlink.Application` mock).
2. Register a mock `Authenticator`/`AuthenticationProvider` whose `FindExternalInitiator` returns a valid EI for supplied `X-Chainlink-EA-AccessKey`/`Secret` headers, and whose `FindUserByAPIToken`/`AuthorizedUserWithSession` fail (no user session/token supplied).
3. Register a mock `chainlink.Application` where `RunJobV2` is a spy capturing `(ctx, jobID, resultVal)` calls.
4. Create a job with some real integer ID (e.g., `42`) unrelated to the EI used, and a distinct EI record in the mock store.
5. Send `POST /v2/jobs/42/runs` with valid EI headers only (no session cookie, no API token headers).
6. Assert response is `200`/success (`respondWithPipelineRun` path reached) and that `RunJobV2` spy was invoked with `jobID == 42`.
7. Assert this contradicts the expected behavior per the code comment ("only users are allowed to run jobs using int IDs - EIs not allowed") — expected correct behavior would be `422 bad job ID` for EI-only credentials, matching how UUID/webhook-based EI runs are supposed to work instead.

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
