### Title
EI credential can bypass "EIs not allowed" int-ID job run check via shared SessionUserKey - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
`PipelineRunsController.Create` gates numeric-ID job execution on `isUser` from `auth.GetAuthenticatedUser(c)`: [1](#0-0) 

However, `AuthenticateExternalInitiator` (the auth method used to authenticate External Initiator (EI) credentials on this route) unconditionally sets the *same* `SessionUserKey` context value used by regular user sessions, assigning a synthetic `User{Role: clsessions.UserRoleRun}`: [2](#0-1) 

`GetAuthenticatedUser` simply reads `SessionUserKey` and returns `ok=true` whenever *any* value is present there, regardless of whether it originated from a real user session/token or from the EI middleware's synthetic user object: [3](#0-2) 

Because of this, an EI-authenticated request satisfies `isUser == true` in `Create`, so the numeric-ID branch executes and `prc.App.RunJobV2(ctx, jobID, nil)` is invoked exactly as if it were a real user — directly contradicting the code comment "only users are allowed to run jobs using int IDs - EIs not allowed". The comment's intended access boundary is not actually enforced because both authentication paths populate the identical context key that the check relies on.

### Impact Explanation
An attacker holding only a valid External Initiator credential (not a user session/API token) can trigger execution of *any* job by numeric ID via `POST /v2/jobs/:ID/runs`, not just the specific job the EI was bound to run via its intended UUID/webhook-registration flow. This is an authorization-bypass / unauthorized job run — matching Chainlink's "unauthorized job run" bounty impact class — because it lets an EI-scoped credential act with a broader capability (arbitrary numeric job ID execution) than the intended restriction described in the code.

### Likelihood Explanation
The only precondition is possession of a valid, already-provisioned External Initiator access key/secret pair (a credential class explicitly in scope as "restricted... external-initiator credential holder"). No admin/host access is required, and the bypass is deterministic and repeatable on every request — the middleware unconditionally sets `SessionUserKey` for every successfully authenticated EI request, so `isUser` is always `true` for EIs on this route.

### Recommendation
Track user-vs-EI authentication origin with a distinct, non-overloaded context key (e.g., a boolean `SessionIsExternalInitiatorKey`, or wrap the synthetic user in a distinguishable type) instead of reusing `SessionUserKey` for both real users and EIs. In `PipelineRunsController.Create`, check that distinct signal (or explicitly check `auth.GetAuthenticatedExternalInitiator(c)` is absent) before allowing the numeric-ID `RunJobV2` path, so the enforcement matches the stated intent.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a test server with the `/v2/jobs/:ID/runs` route wired through `auth.Authenticate` using `AuthenticateExternalInitiator` (mirroring production router wiring in `core/web/router.go`).
2. Provision a valid `bridges.ExternalInitiator` record with known access key/secret, and a job with numeric ID (e.g., `5`).
3. POST `/v2/jobs/5/runs` with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set to the EI's credentials (no user session/API token).
4. Assert current (buggy) behavior: response is `200`/`201` with a pipeline run resource, and `RunJobV2` was invoked — demonstrating the bypass.
5. Expected/fixed behavior: response should be `422` (or `401`) and `RunJobV2` must never be called for EI-only authenticated requests on numeric IDs, matching the comment's stated restriction.

### Citations

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
