### Title
External Initiator credentials cause `AuthenticateExternalInitiator` to set a synthetic `SessionUserKey`, defeating the "EI not allowed" numeric-job-ID check in `PipelineRunsController.Create` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` gates numeric-ID job runs behind `isUser := auth.GetAuthenticatedUser(c)` to explicitly exclude external initiators (EIs) from triggering runs by job ID. However, `auth.AuthenticateExternalInitiator` unconditionally calls `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` on successful EI authentication, which is the exact same context key that `GetAuthenticatedUser` reads. This means a holder of valid EI credentials alone (no forged/valid session cookie required) can cause `isUser` to evaluate `true` and reach `RunJobV2` for a numeric job ID, defeating the intended restriction.

### Finding Description
`PipelineRunsController.Create` derives its "is this a user, not an EI" decision purely from the presence of a `*clsessions.User` in the gin context: [1](#0-0) 

`auth.GetAuthenticatedUser` simply reads `SessionUserKey` from context: [2](#0-1) 

But `AuthenticateExternalInitiator`, upon successfully validating EI access-key/secret headers, deliberately writes a fabricated `User{Role: UserRoleRun}` into that same `SessionUserKey`, in order to satisfy unrelated `RequiresRunRole` checks elsewhere: [3](#0-2) 

The multi-method `Authenticate` middleware tries each `authMethod` in order and stops at the first one that does not return `auth.ErrorAuthFailed`: [4](#0-3) 

If the route protecting `/v2/jobs/:ID/runs` is registered with a method chain that includes both `AuthenticateBySession` and `AuthenticateExternalInitiator` (consistent with the explicit in-handler comment "only users are allowed to run jobs using int IDs - EIs not allowed", which only makes sense if EIs can reach this handler through the same route), then:
- If no session cookie is present, `AuthenticateBySession` returns `auth.ErrorAuthFailed` (session key not found), and the chain falls through to `AuthenticateExternalInitiator`.
- With valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers, `AuthenticateExternalInitiator` succeeds and sets `SessionUserKey` to a synthetic run-role `User`.
- `PipelineRunsController.Create` then reads `isUser == true` and executes `RunJobV2` for the numeric job ID — exactly the behavior the code comment says should be blocked for EIs.

Note: the "forged/expired session cookie" detail in the proof idea is not actually necessary to trigger the confusion — simply omitting the session cookie (or supplying one with no `SessionIDKey` in the session store) is sufficient for `AuthenticateBySession` to short-circuit with `ErrorAuthFailed` and fall through to the EI method. A cookie containing a stale/forged session ID that actually resolves in the session lookup would instead return `ErrUserSessionExpired` (not `ErrorAuthFailed`), which breaks the loop immediately per line 162 and results in a straight 401 — so that specific combination does not bypass the check. The actual root cause is architectural: `AuthenticateExternalInitiator` conflates "EI identity" with "User identity" by writing into `SessionUserKey`, which is the sole signal `PipelineRunsController.Create` uses to distinguish EI callers from Users.

### Impact Explanation
An attacker in possession of only EI credentials (external-initiator role, not a User account) can trigger arbitrary job runs by numeric job ID via `RunJobV2`, which the code explicitly intends to forbid. This is an authorization/role-confusion bypass: EI credentials are meant to be restricted to specific initiator-triggered flows, not general numeric-ID job execution, and this bypass could be used to trigger unintended on-chain job executions (e.g., fund-moving jobs) — matching Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
The precondition is holding valid EI credentials only (no user session or admin access needed) and knowing/guessing a numeric job ID. Sending the request without a session cookie (the normal case for API/EI clients that don't have browser-managed cookies) is trivial and repeatable; no cookie forgery is even required in the primary exploit path.

### Recommendation
Do not reuse `SessionUserKey` to represent EI identity. `AuthenticateExternalInitiator` should use a distinct context key exclusively for the synthetic run-role placeholder needed by `RequiresRunRole`, without polluting `SessionUserKey`/`GetAuthenticatedUser`. Alternatively, `PipelineRunsController.Create` should check `GetAuthenticatedExternalInitiator(c)` and explicitly reject if an EI identity is present, regardless of whether a `SessionUserKey` value also exists.

### Proof of Concept
Handler-integration test plan (Go, using `httptest`):
1. Build a gin router with `auth.Authenticate(mockAuthenticator, auth.AuthenticateBySession, auth.AuthenticateExternalInitiator)` wrapping `PipelineRunsController.Create`, matching the app's real route config for `/v2/jobs/:ID/runs`.
2. Mock `Authenticator.FindExternalInitiator` to return a valid `bridges.ExternalInitiator` for supplied EI access key/secret, and mock `RunJobV2` on the app to return a run ID if called.
3. Send `POST /v2/jobs/123/runs` with valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and **no session cookie**.
4. Assert: `RunJobV2` was called (demonstrating the "EIs not allowed" numeric-ID guard was bypassed), and response is `200`/pipeline run resource instead of `422 "bad job ID"`.
5. Repeat with a session cookie containing an unrecognized/expired session ID; assert the request is correctly rejected with `401` (confirming the auth-chain break-on-non-ErrorAuthFailed behavior), to distinguish this from the no-cookie case above.

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

**File:** core/web/auth/auth.go (L157-175)
```go
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
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
