### Title
External Initiator credentials satisfy `isUser` check in `PipelineRunsController.Create`, allowing EI-authenticated requests to trigger arbitrary jobs by integer ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`auth.AuthenticateExternalInitiator` stores a synthetic `*sessions.User{Role: UserRoleRun}` under the same `SessionUserKey` used for real session/token users. `PipelineRunsController.Create`'s `isUser` gate (`auth.GetAuthenticatedUser`) cannot distinguish this synthetic EI user from a genuine authenticated user, so a caller holding only valid EI credentials can hit `POST /v2/jobs/:ID/runs` with a numeric job ID and trigger `RunJobV2` for any job, not just jobs explicitly bound to that EI via `ExternalInitiatorWebhookSpecs`.

### Finding Description
The shared route is registered without distinguishing EI vs interactive-user origin: [1](#0-0) 

`AuthenticateExternalInitiator` sets `SessionUserKey` to a synthetic `User{Role: UserRoleRun}` in addition to `SessionExternalInitiatorKey`: [2](#0-1) 

`RequiresRunRole` only checks the role stored under `SessionUserKey`, so the synthetic EI user passes: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code explicitly comments "only users are allowed to run jobs using int IDs - EIs not allowed," but the actual check (`auth.GetAuthenticatedUser`) returns `true` for the EI's synthetic user just as it would for a real session/token user, since both are stored identically under `SessionUserKey`: [4](#0-3) 

This means an attacker with only valid EI `AccessKey`/`Secret` headers can POST to `/v2/jobs/<intJobID>/runs` and reach `app.RunJobV2(ctx, jobID, nil)` for any numeric job ID, completely bypassing the intended exactness guarantee that EIs may only trigger jobs bound to them via `ExternalInitiatorWebhookSpecs`.

Mitigating factor: `RunJobV2` itself is gated by a build check that blocks all callers (including legitimate admin/edit users) on production/secure builds: [5](#0-4) 

Additionally, the webhook job type (the only job type historically associated with `ExternalInitiatorWebhookSpecs`) has been removed from the codebase entirely (`job.ErrJobTypeRemoved`), so EIs no longer have any legitimately-bound job to trigger by design — any EI-triggered run via a numeric ID is categorically outside intended use, confirming this is a true authorization-exactness violation rather than a benign overlap.

### Impact Explanation
On non-production ("dev"/"test") builds, an attacker holding only EI credentials (not requiring any user session, API token, or admin privilege) can invoke arbitrary job runs by integer job ID via `RunJobV2`, including VRF and OCR-pipeline jobs, causing unauthorized job execution, resource consumption, and potentially triggering on-chain side effects from pipeline tasks (e.g., bridge calls, HTTP requests) outside the EI's authorized scope. This matches the "unauthorized job run" bounty impact class. The impact is scoped to non-production builds since `build.IsProd()` blocks `RunJobV2` entirely in secure builds.

### Likelihood Explanation
Requires only valid EI credentials (`ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader`), obtainable by any party the node operator has granted for even a single, unrelated legitimate integration. No admin, session, or token access needed. Exploit is trivially repeatable (single HTTP POST) but only reachable when the node is running a non-prod/dev build (`build.IsProd() == false`), which limits real-world exposure since production Chainlink nodes are expected to run secure builds.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject requests where `auth.GetAuthenticatedExternalInitiator(c)` succeeds (i.e., the request originated from EI authentication) before allowing the integer-ID `isUser` branch, rather than relying solely on `GetAuthenticatedUser`. Alternatively, have `AuthenticateExternalInitiator` set a distinct context marker (not reusing `SessionUserKey`) so `isUser`-style checks cannot be satisfied by EI-derived pseudo-users.

### Proof of Concept
Go handler-level integration test plan:
1. Start `cltest` application with a non-prod build tag (default test build already has `build.IsProd() == false`).
2. Create a non-webhook job (e.g., a minimal cron or OCR bootstrap-free job) via `cltest.CreateJobViaWeb`, obtaining an integer `job.ID`.
3. Create an External Initiator via `cltest.CreateExternalInitiatorViaWeb` and capture its `AccessKey`/`Secret`.
4. POST to `/v2/jobs/<intJobID>/runs` using only the `ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader` (no session cookie, no API token).
5. Assert the response is `200`/success and that `pipeline_runs` table row count increases (or mock `app.RunJobV2` invocation), proving the EI-authenticated request successfully triggered a run for a job not bound to it via `ExternalInitiatorWebhookSpecs`.
6. Compare against expected behavior: response should be `401`/`422` rejecting EI-origin numeric-ID run requests.

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

**File:** core/services/chainlink/application.go (L1125-1133)
```go
// Only used for local testing, not supported by the UI.
func (app *ChainlinkApplication) RunJobV2(
	ctx context.Context,
	jobID int32,
	meta map[string]any,
) (int64, error) {
	if build.IsProd() {
		return 0, errors.New("manual job runs not supported on secure builds")
	}
```
