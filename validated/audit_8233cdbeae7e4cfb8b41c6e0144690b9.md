### Title
External Initiator credentials can trigger integer-keyed job runs via `PipelineRunsController.Create` due to shared session-user context key - ([File: core/web/pipeline_runs_controller.go])

### Summary
`v2Routes` registers `POST /v2/jobs/:ID/runs` behind `userOrEI` group, which accepts `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession`, gated only by `auth.RequiresRunRole` [1](#0-0) . `AuthenticateExternalInitiator` injects a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` into the same `SessionUserKey` context slot used for real authenticated users [2](#0-1) . Inside `PipelineRunsController.Create`, the code comment states "only users are allowed to run jobs using int IDs - EIs not allowed", but the actual check `_, isUser := auth.GetAuthenticatedUser(c)` returns `true` for External Initiators too, since it reads the same shared `SessionUserKey`, allowing EI-authenticated requests to reach `prc.App.RunJobV2` for any integer job ID [3](#0-2) .

### Finding Description
The intended authorization model, per the comment in `Create`, is that External Initiators may only trigger job runs via their legacy UUID-based webhook mechanism (now removed, returning `job.ErrJobTypeRemoved`), while integer job IDs should be runnable only by genuine dashboard/API users [4](#0-3) .

However, the discriminator used, `auth.GetAuthenticatedUser(c)`, simply checks whether `SessionUserKey` is set in the gin context and type-asserts it to `*clsessions.User` [5](#0-4) . `AuthenticateExternalInitiator` explicitly sets this same key to a synthetic run-role `User` object so that `RequiresRunRole` (and other role-gated wrappers reused across `userOrEI`) can pass EI requests [6](#0-5) . As a side effect, this makes `isUser` evaluate to `true` for EI-authenticated requests as well, since there is no separate marker distinguishing a "real" session/token user from the synthetic EI user (the correct check would have been `auth.GetAuthenticatedExternalInitiator(c)` to explicitly detect and reject EI callers).

Attack flow:
1. Attacker holds valid External Initiator credentials (`OCR-EI-ACCESSKEY`/`OCR-EI-SECRET` headers), which is one of the explicitly allowed unprivileged attacker profiles ("external-initiator credential holder").
2. Attacker sends `POST /v2/jobs/<integer-job-id>/runs` with the EI headers.
3. `auth.Authenticate` runs `AuthenticateExternalInitiator`, which succeeds and sets `SessionUserKey` to a synthetic `UserRoleRun` user [7](#0-6) .
4. `auth.RequiresRunRole` passes because `user.Role == UserRoleRun` [8](#0-7) .
5. `PipelineRunsController.Create` evaluates `isUser := true` (from the synthetic user), parses the integer ID, and calls `prc.App.RunJobV2(ctx, jobID, nil)`, triggering execution of the job pipeline for that job ID regardless of job type [9](#0-8) .

This defeats the intended restriction that EIs should not be able to trigger arbitrary integer-ID job runs, since the only enforced gate (`isUser`) is trivially satisfied by any EI-authenticated request.

### Impact Explanation
Any holder of valid External Initiator credentials for the node (a low-privilege credential class, not intended to trigger arbitrary jobs by ID) can force execution of any job's pipeline run by integer ID, including jobs unrelated to that External Initiator's own bridge/webhook. Depending on job configuration (e.g., VRF fulfillment, Keeper/Automation, OCR-triggering jobs, or jobs with `ethtx` tasks), this can cause unauthorized on-chain transactions/fund movement, resource exhaustion via repeated triggering, or unintended side effects on jobs the EI was never authorized to run — matching the "unauthorized job run" and "authorization bypass" impact classes.

### Likelihood Explanation
Exploitation requires only valid External Initiator access key/secret (a credential level explicitly listed as an eligible unprivileged attacker in this audit), no admin/session/user credentials. The request is a single unauthenticated-relative-to-jobs HTTP POST, fully repeatable, and does not depend on timing races, network position, or misconfiguration — it is a direct logic flaw in the role/identity discrimination code.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject External-Initiator-authenticated callers instead of relying on `GetAuthenticatedUser` alone — e.g., check `if _, isEI := auth.GetAuthenticatedExternalInitiator(c); isEI { return error }` before allowing the integer-ID `RunJobV2` path, or mark the synthetic EI user distinctly (e.g., a dedicated `Role`/flag not indistinguishable from a genuine session/token user) so downstream handlers can differentiate identity type from role.

### Proof of Concept
Handler-level integration test plan (Go, `core/web/pipeline_runs_controller_test.go` style):
1. Set up a test app with an authenticated `ExternalInitiator` record (headers `OCR-EI-ACCESSKEY`/`OCR-EI-SECRET`) and a job with an integer ID (e.g., a webhook or directrequest job not tied to that EI).
2. Send `POST /v2/jobs/<jobID>/runs` using only the EI headers (no session cookie, no API token).
3. Assert the response status is `200 OK` with a `pipelineRun` resource returned (current buggy behavior) instead of an authorization error.
4. Assert (via mock/stub on `App.RunJobV2`) that `RunJobV2` was actually invoked with `jobID`, proving pipeline execution was triggered by the EI credential alone.
5. Add a regression assertion: after the fix, the same request should return `401/403` and `RunJobV2` should not be called.

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

**File:** core/web/pipeline_runs_controller.go (L101-107)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
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
