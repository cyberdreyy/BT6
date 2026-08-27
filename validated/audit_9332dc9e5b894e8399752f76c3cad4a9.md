### Title
External-initiator credentials bypass job binding and can trigger runs on arbitrary jobs via `POST /v2/jobs/:ID/runs` - ([File: core/web/pipeline_runs_controller.go])

### Summary
The `/v2/jobs/:ID/runs` route is reachable by both authenticated users and external initiators via `auth.AuthenticateExternalInitiator` [1](#0-0) . `PipelineRunsController.Create` attempts to restrict integer job-ID runs to real users only ("EIs not allowed"), but the guard it uses, `auth.GetAuthenticatedUser`, also returns `ok=true` for external-initiator-authenticated requests because `AuthenticateExternalInitiator` unconditionally sets the same `SessionUserKey` context value used by real users.

### Finding Description
The router wires `POST /v2/jobs/:ID/runs` behind a group authenticated with `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, or `auth.AuthenticateBySession`, and wraps the handler in `auth.RequiresRunRole` [1](#0-0) .

`auth.AuthenticateExternalInitiator` only verifies the external initiator's own access key/secret against the `external_initiators` table — it never binds the request to a specific job. After successful verification it sets both context keys:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [2](#0-1) 

In `PipelineRunsController.Create`, the code comments state that only real users (not external initiators) may trigger runs using an integer job ID:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [3](#0-2) 

But `auth.GetAuthenticatedUser` simply reads `SessionUserKey` from the gin context:
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	...
}
``` [4](#0-3) 

Since `AuthenticateExternalInitiator` also sets `SessionUserKey`, `isUser` is `true` for external-initiator-authenticated requests too — the intended EI exclusion is dead code. Any holder of valid external-initiator credentials (`X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers) can therefore call `RunJobV2(ctx, jobID, nil)` with **any** integer job ID, not just a job that was configured to be triggered by that specific initiator. There is no lookup of an `ExternalInitiatorWebhookSpec` or any other job-to-initiator association check anywhere in this path.

### Impact Explanation
This is a request-to-job binding / authorization bypass: an external-initiator credential (which is intended to be scoped to trigger a specific webhook job) can instead trigger a run of any job on the node by supplying an arbitrary integer job ID. Depending on job type, this can cause unauthorized job execution, unintended external effects (e.g., triggering flux monitor/VRF/other job pipeline tasks, on-chain transactions, or external HTTP calls configured in another user's job), and cross-tenant job-run confusion. This matches the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Exploitation requires only a valid external-initiator access key/secret pair — the same low-privilege credential class explicitly listed as attacker-accessible in scope ("external-initiator credential holder"). No admin/session/API-token privileges are needed. The exploit is a single unauthenticated-relative-to-target-job HTTP `POST` request and is trivially repeatable against any job ID on the node.

### Recommendation
In `PipelineRunsController.Create`, distinguish external-initiator authentication from real user authentication explicitly (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` and reject/short-circuit before falling into the `isUser` branch), rather than relying on `GetAuthenticatedUser`, which is now also populated for external initiators. Alternatively, stop setting `SessionUserKey` in `AuthenticateExternalInitiator`, and have `RequiresRunRole` and any user-role checks explicitly support the external-initiator identity in a way that is distinguishable from a real logged-in user with `Run` role.

### Proof of Concept
Go handler-level integration test plan:
1. Seed two jobs, `jobA` (int ID 1) and `jobB` (int ID 2), belonging to different owners/initiators; do not associate any external initiator with `jobB`.
2. Create an `ExternalInitiator` record with access key/secret, associated (conceptually) only with `jobA`.
3. Using an `httptest` gin router built via `web.NewRouter`, send `POST /v2/jobs/2/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to the external initiator's credentials (no session cookie, no API token).
4. Assert the response is `200 OK` with a `pipelineRun` resource created for `jobB` (ID 2) — i.e., `prc.App.RunJobV2` was invoked with `jobID == 2`.
5. Assert this succeeds despite the initiator never having been configured for `jobB`, proving the missing job binding.
6. As a control, add a unit test on `auth.GetAuthenticatedUser` demonstrating that after `AuthenticateExternalInitiator` runs, `GetAuthenticatedUser(c)` returns `ok == true`, confirming the root cause.

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

**File:** core/web/auth/auth.go (L119-151)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
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
