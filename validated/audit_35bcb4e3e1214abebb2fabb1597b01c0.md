### Title
External Initiator credentials can trigger runs on ANY job by integer ID with no binding to the EI's own webhook spec - ([File: core/web/pipeline_runs_controller.go])

### Summary
`POST /v2/jobs/:ID/runs` is reachable by any valid External-Initiator (EI) credential because `AuthenticateExternalInitiator` unconditionally injects a synthetic `clsessions.User{Role: UserRoleRun}` into the Gin context, and `PipelineRunsController.Create` mistakenly treats the mere *presence* of that user object as proof of "real user" authorization. `RunJobV2` is then called with the raw integer job ID and no verification that the ID corresponds to a webhook job whose `ExternalInitiatorSpec` matches the authenticated EI's name/URL.

### Finding Description
The route is registered in the `userOrEI` group with the run-role gate: [1](#0-0) 

`AuthenticateExternalInitiator` authenticates purely by EI AccessKey/Secret and then forcibly sets the session user role to `UserRoleRun`, using the same context key (`SessionUserKey`) that real user session/token authentication uses: [2](#0-1) 

`RequiresRunRole` only checks `user.Role != clsessions.UserRoleView`, so the EI's synthetic Run-role user passes trivially: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code comment explicitly states "only users are allowed to run jobs using int IDs - EIs not allowed," but the enforcement logic only checks `isUser` via `auth.GetAuthenticatedUser(c)`, which returns `true` for *any* context that has a `SessionUserKey` set — including the EI-injected fake user: [4](#0-3) 

Because `AuthenticateExternalInitiator` sets `SessionUserKey` (line 148 of `auth.go`), `GetAuthenticatedExternalInitiator`/`GetAuthenticatedUser` cannot distinguish "real user" from "EI masquerading as user." The intended guard (block EIs from int-ID job runs) is bypassed, and `RunJobV2(ctx, jobID, nil)` is invoked with attacker-controlled `jobID` and no lookup or comparison against the EI's own `bridges.ExternalInitiator` record (`GetAuthenticatedExternalInitiator` is never called in this handler or anywhere outside `auth.go`). Consequently, any valid EI credential (e.g., for initiator "foo") can POST to `/v2/jobs/<barsJobID>/runs` and trigger a run belonging to a completely different initiator or a user-only job — there is no ORM check binding the specific EI's registered name/URL to the target job's webhook spec.

### Impact Explanation
This is an authorization-bypass leading to unauthorized triggering of arbitrary job runs (a "run" invariant violation): the codebase intends "requests are bound to exactly one authorized job" per EI, but any EI credential can invoke pipeline runs for any integer job ID system-wide, including jobs owned by other initiators or non-webhook jobs. Depending on job configuration this can cause unauthorized on-chain transactions/fund movement, resource exhaustion, or interference with other tenants' job executions — matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Requires only a single valid, low-privilege EI AccessKey/Secret pair (issued to any external initiator) — no admin/operator access needed. The bypass is deterministic and fully repeatable: any EI, once registered, can enumerate/guess integer job IDs and trigger runs, since `RunJobV2` does not validate the caller's binding to the job.

### Recommendation
Distinguish EI-authenticated requests from genuine user sessions in `PipelineRunsController.Create` (e.g., check for the presence of `SessionExternalInitiatorKey` via `auth.GetAuthenticatedExternalInitiator` and reject explicitly, rather than relying on `GetAuthenticatedUser` returning `true`). Additionally, restore per-EI job binding: before calling `RunJobV2`, look up the target job's `ExternalInitiatorSpec` and verify it matches the authenticated EI's name/URL from `store.FindExternalInitiator`.

### Proof of Concept
Go handler-level integration test plan:
1. Create two external initiators, `foo` and `bar`, each via `ExternalInitiatorsController` / `bridges.NewExternalInitiator`.
2. Create a job (webhook or otherwise) owned/associated with `bar`, recording its integer `jobID`.
3. Authenticate as EI `foo` (headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`).
4. Send `POST /v2/jobs/<bar's jobID>/runs` with EI `foo`'s credentials.
5. Assert: expected behavior is `401`/`422` (rejected, since `foo` isn't bound to that job); actual behavior with current code is `200`/`201` with a `pipelineRun` resource created, confirming missing binding and role-check bypass via `PipelineRunsController.Create`'s flawed `isUser` check.

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

**File:** core/web/pipeline_runs_controller.go (L101-128)
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

	jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
}
```
