### Title
EI credentials can trigger job runs via int job IDs, contradicting the "EIs not allowed" restriction in `PipelineRunsController.Create` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` gates int-ID job runs on `isUser := auth.GetAuthenticatedUser(c)` being true, with a comment claiming this excludes External Initiators (EIs). However, `AuthenticateExternalInitiator` explicitly sets `SessionUserKey` to a synthetic `&clsessions.User{Role: UserRoleRun}` in addition to `SessionExternalInitiatorKey`, so `GetAuthenticatedUser` returns `ok=true` for EI-authenticated requests, making `isUser` true and allowing EIs to trigger `RunJobV2` via integer job IDs.

### Finding Description
In `core/web/pipeline_runs_controller.go` `Create`: [1](#0-0) 

the code checks `_, isUser := auth.GetAuthenticatedUser(c)` and comments "only users are allowed to run jobs using int IDs - EIs not allowed". But `AuthenticateExternalInitiator` in `core/web/auth/auth.go`: [2](#0-1) 

sets both `SessionExternalInitiatorKey` and `SessionUserKey` (to a `User{Role: UserRoleRun}`) on successful EI credential validation. `GetAuthenticatedUser` simply reads `SessionUserKey`: [3](#0-2) 

so it returns `ok=true` regardless of whether the caller authenticated as a real user or as an EI. This means the `isUser` check in `Create` does not actually distinguish EIs from users — it passes for both. Since the earlier UUID-based webhook path was removed (`job.ErrJobTypeRemoved`), the only intended distinction left in the handler is int-ID vs UUID, and the comment implies EIs should be blocked from int-ID runs, but the code doesn't implement that; it only checks whether *some* authenticated identity is present, which is always true for EIs by design.

### Impact Explanation
This allows a caller holding only an External Initiator access key/secret (not a full user session or user API token) to invoke `RunJobV2` on arbitrary job IDs by integer ID, exactly the action the code comment says should be restricted to users. This maps to an authorization/role-bypass issue — an EI credential holder gains job-run capability equivalent to a `run`-role user across any job ID reachable via this route, not just jobs associated with that specific EI. This could allow unauthorized triggering of job runs (potentially including fund-moving or oracle-request jobs) using only EI credentials rather than the intended broader webhook-specific mechanism.

### Likelihood Explanation
Preconditions: attacker must possess valid EI credentials (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` or equivalent headers matched by `FindExternalInitiator`/`AuthenticateExternalInitiator`). No user session, user API token, or elevated role is required beyond a valid EI record. This is fully feasible/repeatable given valid EI credentials, which are a lower-privilege credential type than user accounts. I could not fully verify from the router configuration which specific auth middleware chain(s) are mounted in front of this particular route (i.e., whether `AuthenticateExternalInitiator` is actually included among the methods used for `POST /v2/jobs/:ID/runs`) — the router grep only returned partial context and I was unable to read `core/web/router.go` route wiring in full before running out of iterations. This should be confirmed before treating the finding as fully proven end-to-end.

### Recommendation
In `Create`, explicitly reject requests authenticated via `AuthenticateExternalInitiator` for the int-ID path by checking `auth.GetAuthenticatedExternalInitiator(c)` and rejecting if present, rather than relying solely on `GetAuthenticatedUser`, e.g.:
```go
if _, isEI := auth.GetAuthenticatedExternalInitiator(c); isEI {
    jsonAPIError(c, http.StatusUnauthorized, errors.New("external initiators may not run jobs by ID"))
    return
}
```
Alternatively, avoid mutating `SessionUserKey` in `AuthenticateExternalInitiator` and use a distinct context key/role check so `GetAuthenticatedUser` cannot be spoofed by EI auth.

### Proof of Concept
1. Build a `gin.Context` with a request to `POST /v2/jobs/123/runs`.
2. Manually populate the context as `AuthenticateExternalInitiator` does: `c.Set(auth.SessionExternalInitiatorKey, ei)` and `c.Set(auth.SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})`.
3. Call `PipelineRunsController.Create(c)` with a mocked `chainlink.Application` where `RunJobV2` is expected to be called with `jobID=123`.
4. Assert `auth.GetAuthenticatedUser(c)` returns `ok=true` (confirming `isUser=true`).
5. Assert `RunJobV2` was invoked and the handler returned `200 OK` with a pipeline run resource, despite only EI credentials being used — demonstrating the comment's intended restriction is not enforced.
6. (Integration-level, if router details confirmed) Send an HTTP request with only EI headers to `POST /v2/jobs/:ID/runs` against a running node and confirm a job run is triggered.

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
