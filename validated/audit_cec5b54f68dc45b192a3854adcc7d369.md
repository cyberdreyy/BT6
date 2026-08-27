### Title
External Initiator credential holder can run arbitrary integer-ID jobs via `POST /v2/jobs/:ID/runs`, bypassing the "EIs not allowed" comment - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value used for real logged-in users, assigning a synthetic `clsessions.User{Role: UserRoleRun}`. `PipelineRunsController.Create` gates its int32-job-run logic on `auth.GetAuthenticatedUser(c)` returning `ok`, which is also true for EI-authenticated requests, directly contradicting the inline comment "only users are allowed to run jobs using int IDs - EIs not allowed".

### Finding Description
The route is registered once, not twice: `userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))` [1](#0-0) , using an auth group that accepts `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession`. The `authv2` group only exposes GET routes for pipeline runs (`GET /jobs/:ID/runs`, `GET /jobs/:ID/runs/:runID`), not POST [2](#0-1) , so the "registered twice" premise in the question is factually incorrect.

However, the real issue is in `AuthenticateExternalInitiator`, which explicitly comments that EI endpoints "inherently assume the role of 'run'" and sets `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` in addition to `SessionExternalInitiatorKey` [3](#0-2) . This means `auth.GetAuthenticatedUser(c)`, which only checks presence of `SessionUserKey`, returns `ok=true` for EI-authenticated requests just as it would for a real session/token user [4](#0-3) .

In `PipelineRunsController.Create`, the code explicitly comments "only users are allowed to run jobs using int IDs - EIs not allowed" and gates the int32 `RunJobV2` path on `isUser` from `auth.GetAuthenticatedUser(c)` [5](#0-4) . Since EI-authenticated requests populate the same key, `isUser` is `true` for EIs as well, and the int-ID branch executes for EI credentials — the opposite of the stated intent. The UUID check earlier in the function only blocks webhook-UUID-style IDs (and that job type has since been removed) [6](#0-5) ; it does nothing to distinguish EI from user callers for int IDs.

`RequiresRunRole` only checks the middleware sees a non-`UserRoleView` role and does not check whether the caller is an EI or a real user [7](#0-6) , so it does not stop this.

### Impact Explanation
An attacker holding only a valid External-Initiator access key/secret (a low-privilege, non-admin credential, typically distributed to third-party webhook integrations) can trigger job runs for any int32 job ID on the node by sending `POST /v2/jobs/<jobID>/runs` with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-AccessKey-Secret` headers. This is an authorization/identity-confusion bypass: EI credentials, meant only to be usable for their own webhook trigger, can instead invoke arbitrary jobs via `prc.App.RunJobV2`, potentially causing unauthorized on-chain transactions, unwanted resource consumption, or interference with unrelated job pipelines — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Exploitation requires only a valid, unprivileged external-initiator access key/secret pair (obtainable by any party the node operator registered as an EI, or leaked from an EI integration) — no admin/session/API-token credential is needed. The request is a single unauthenticated (from the node's perspective, EI-authenticated) HTTP POST with a guessable/enumerable integer job ID, making this reliably repeatable.

### Recommendation
Distinguish real users from EI-derived pseudo-users. Either:
1. Do not use `SessionUserKey`/`GetAuthenticatedUser` to represent EI callers; instead check `auth.GetAuthenticatedExternalInitiator(c)` and explicitly reject EI callers in `PipelineRunsController.Create`'s int-ID branch, restoring the documented "EIs not allowed" invariant, or
2. If EI-triggered runs are intended to be supported, replace the ambiguous synthetic-user mechanism with an explicit, job-scoped authorization (e.g., verify the EI is associated with the specific job ID being run) rather than granting a blanket `UserRoleRun` identity indistinguishable from real users.

### Proof of Concept
Handler-level integration test in `core/web/pipeline_runs_controller_test.go`:
1. Set up a test app with a registered job (int32 ID) and a registered External Initiator with known access key/secret, per existing helpers in the test file.
2. Issue `POST /v2/jobs/<jobID>/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to the EI's credentials (no session cookie, no API token).
3. Assert the response is `200 OK` with a `pipelineRun` JSON:API resource (i.e., `prc.App.RunJobV2` was invoked), rather than the expected `422 Unprocessable Entity` ("bad job ID") that the "EIs not allowed" comment implies should occur.
4. As a control, assert the same request without valid EI/user credentials fails with `401 Unauthorized`, confirming the run only succeeds due to EI-derived `SessionUserKey` presence.

### Citations

**File:** core/web/router.go (L399-401)
```go
		authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs/:runID", prc.Show)
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

**File:** core/web/pipeline_runs_controller.go (L103-107)
```go
	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
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
