### Title
EI-issued pseudo-user identity is indistinguishable from a real session/token user, allowing bridge-scoped EI credentials to trigger arbitrary jobs via `POST /v2/jobs/:ID/runs` - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` stores an EI-authenticated request's identity in the same context key (`SessionUserKey`) used for real session/token users, with blanket `UserRoleRun`. `PipelineRunsController.Create` relies on the presence of that key (`auth.GetAuthenticatedUser`) to decide "only users are allowed to run jobs using int IDs - EIs not allowed," but since the EI middleware sets the identical key, this check does not actually distinguish EIs from real users, and no job/bridge-specific binding is verified.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go`, lines 116-151) authenticates the EI credential against `bridges.ExternalInitiator` and, on success, does: [1](#0-0) 
It sets both `SessionExternalInitiatorKey` and `SessionUserKey` — the latter with a synthetic `User{Role: UserRoleRun}` that carries no reference to which EI/bridge issued it.

`GetAuthenticatedUser` (same file) simply checks for the presence of `SessionUserKey`: [2](#0-1) 
It cannot tell whether the identity came from a real session/API-token user or from an EI.

The route `POST /v2/jobs/:ID/runs` is registered in the `userOrEI` group that accepts EI, token, or session auth, then wraps the handler with `RequiresRunRole`: [3](#0-2) 
`RequiresRunRole` only checks `user.Role != UserRoleView`: [4](#0-3) 
— it grants pass-through to the synthetic EI-user because its role is `UserRoleRun`.

Inside the handler, `PipelineRunsController.Create` attempts to gate integer-ID job runs to "real users" only: [5](#0-4) 
The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which returns `true` for the EI-authenticated pseudo-user set by `AuthenticateExternalInitiator`. There is no call to `auth.GetAuthenticatedExternalInitiator(c)` to reject EI-originated requests, and there is no check that ties the target job ID to the specific EI/bridge that authenticated the request. The former EI-to-job binding mechanism (webhook jobs keyed by UUID) has been removed, as shown by the UUID-rejection branch just above: [6](#0-5) 
leaving no replacement binding check for the integer-ID path.

As a result, an EI credential issued for one bridge/webhook can authenticate to `/v2/jobs/:ID/runs` for ANY integer job ID and trigger `prc.App.RunJobV2(ctx, jobID, nil)`, bypassing any intended bridge-specific scoping.

### Impact Explanation
This is an authorization/allowlist bypass: a low-privilege credential meant only to trigger a specific bridge's webhook callback can instead trigger runs of arbitrary jobs on the node, which may include jobs that move funds, call external adapters, or affect on-chain state depending on job configuration. This matches Chainlink's "unauthorized job run" bounty impact class, since job execution is not properly scoped to the credential that authenticates it.

### Likelihood Explanation
Precondition: attacker must already hold valid EI credentials (access key + secret) for the node — these are typically distributed to less-trusted bridge/adapter operators specifically because they are meant to be narrowly scoped to trigger only their own bridge's callback. No admin/edit/view role, database, or host access is needed beyond possessing one legitimate EI credential pair. The bypass is deterministic and repeatable — any request with valid EI headers reaching `/v2/jobs/:ID/runs` with an arbitrary numeric job ID will be treated as a `run`-role user request.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject requests where `auth.GetAuthenticatedExternalInitiator(c)` returns an EI object (i.e., check the actual auth method, not just presence of a synthetic `SessionUserKey`), rather than relying on `GetAuthenticatedUser`. If EIs must be allowed to trigger runs at all, bind the request's EI identity to the specific job/bridge it's authorized for (verify the job's associated bridge/EI matches `ei.Name`) before invoking `RunJobV2`. More broadly, avoid populating `SessionUserKey` with a synthetic user in `AuthenticateExternalInitiator`; instead have `RequiresRunRole` and handlers check EI identity through `SessionExternalInitiatorKey` distinctly from real user sessions.

### Proof of Concept
Handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go`):
1. Set up an application with two jobs, `jobA` (int ID) and `jobB` (int ID), and create an `ExternalInitiator` record intended only for `jobA`'s bridge (via `bridges.NewExternalInitiator`/`CreateExternalInitiator`).
2. Build an HTTP client that sets `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers to the EI's credentials (matching `static.ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader`).
3. Send `POST /v2/jobs/{jobB.ID}/runs` using the EI credentials scoped to `jobA`.
4. Assert expectation: request should be rejected (e.g., 401/403) because the EI is not authorized for `jobB`.
5. Observe actual current behavior: `isUser` evaluates true (via `GetAuthenticatedUser`), `RequiresRunRole` passes (role is `UserRoleRun`), and `RunJobV2` is invoked successfully for `jobB`, returning `201 Created` with a pipeline run resource — demonstrating the bypass.
6. Add a Go unit test on `auth.GetAuthenticatedUser` after invoking `AuthenticateExternalInitiator` directly on a `gin.Context`, asserting it returns `true` even though no real user/session/API-token existed, confirming the root cause of the confusion between EI and real user identities.

### Citations

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

**File:** core/web/router.go (L450-457)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
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
