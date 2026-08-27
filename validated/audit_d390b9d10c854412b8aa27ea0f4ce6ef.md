### Title
External Initiator authentication bypasses job binding on `POST /v2/jobs/:ID/runs`, allowing any EI credential to trigger job runs for any job - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` unconditionally sets `SessionUserKey` to a `UserRoleRun` user regardless of which External Initiator authenticated [1](#0-0) . `PipelineRunsController.Create`, reached via the `userOrEI` route group protected by `RequiresRunRole`, only checks `isUser := auth.GetAuthenticatedUser(c)` and then triggers `RunJobV2` on an arbitrary int32 job ID with no verification that the authenticated External Initiator is associated with that job [2](#0-1) .

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered under the `userOrEI` group, authenticated by any of `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession`, and gated only by `RequiresRunRole` [3](#0-2) .

`AuthenticateExternalInitiator` looks up the External Initiator by its own AccessKey/Secret, verifies the HMAC via `bridges.AuthenticateExternalInitiator`, and then sets `SessionExternalInitiatorKey` to that specific EI object — but it also unconditionally sets `SessionUserKey` to a fresh `&clsessions.User{Role: clsessions.UserRoleRun}` with no reference to which EI authenticated [4](#0-3) .

In `PipelineRunsController.Create`, the handler retrieves this user via `auth.GetAuthenticatedUser(c)` purely to gate "only users [and EIs, since both satisfy this check] are allowed to run jobs using int IDs," then parses the URL path job ID as an int32 and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly — there is no lookup of `auth.GetAuthenticatedExternalInitiator(c)` and no comparison against the job's associated External Initiator (e.g., via a webhook spec binding) [2](#0-1) . The comment on line 103-107 notes that legacy UUID-based external-initiator job runs (which presumably enforced per-EI binding) have been removed, leaving only the int32 path with no binding check at all [5](#0-4) .

Because `RequiresRunRole` only checks `user.Role != clsessions.UserRoleView` [6](#0-5) , and the synthetic user created for every EI always has `UserRoleRun`, any valid EI credential pair passes both the authentication and role checks for this endpoint regardless of which job it targets.

### Impact Explanation
Any holder of a valid External Initiator AccessKey/Secret pair (a low-privilege, narrowly-scoped credential meant only to trigger runs for its own bound job/webhook) can invoke `POST /v2/jobs/:ID/runs` for *any* job ID on the node, not just the job(s) it was provisioned for. This is an authorization/request-binding bypass: it allows unauthorized triggering of job runs belonging to other tenants/EIs, which can cause unwanted external calls, resource consumption, spurious on-chain transactions triggered by job pipelines, or interference with other integrations' data flows — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Exploitation requires only possession of one valid External Initiator AccessKey/Secret pair, an unprivileged credential by design (many external initiators, even third-party ones, hold such credentials). No admin, no session, no user account is needed. The job ID is a small enumerable integer and endpoint enumeration is straightforward. This is fully reproducible and repeatable on any Chainlink node with more than one External Initiator/job configured.

### Recommendation
In `PipelineRunsController.Create`, when the request was authenticated via `AuthenticateExternalInitiator`, retrieve the External Initiator via `auth.GetAuthenticatedExternalInitiator(c)` and verify that the target job (looked up via `jobID`) is actually bound to that specific External Initiator (e.g., via its webhook/EI spec) before calling `RunJobV2`. Do not rely solely on the synthetic `UserRoleRun` user set by `AuthenticateExternalInitiator` to authorize access to arbitrary job IDs.

### Proof of Concept
1. Create two `bridges.ExternalInitiator` records, EI-A and EI-B, each with distinct AccessKey/Secret.
2. Create two jobs, Job-A (intended for EI-A) and Job-B (intended for EI-B), each with a webhook/EI spec that would need to reference the given initiator.
3. In a `gin` handler-level integration test (extending `pipeline_runs_controller_test.go`), send `POST /v2/jobs/:Job-B-ID/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI-A's credentials.
4. Assert current (buggy) behavior: the request succeeds (HTTP 200 with a `pipelineRun` resource), proving EI-A triggered a run on Job-B despite no binding.
5. Assert expected/fixed behavior: the request should be rejected with HTTP 401/403 because EI-A is not associated with Job-B.

### Citations

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
