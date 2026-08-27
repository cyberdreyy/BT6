### Title
EI credential holder can bypass "EIs not allowed" restriction and trigger job runs via int32 job ID - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
`PipelineRunsController.Create` gates the int32 job-ID execution path on `_, isUser := auth.GetAuthenticatedUser(c)` and comments "only users are allowed to run jobs using int IDs - EIs not allowed" [1](#0-0) . However, `GetAuthenticatedUser` merely checks whether *any* `*clsessions.User` object was placed in the gin context under `SessionUserKey`; it does not distinguish a genuine user session/token from an external-initiator pseudo-user [2](#0-1) .

Critically, `AuthenticateExternalInitiator` — on successful EI credential verification — itself sets `SessionUserKey` to a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` [3](#0-2) . Therefore, once `Authenticate`'s method loop (which tries `AuthenticateBySession`, `AuthenticateByToken`, `AuthenticateExternalInitiator` in order and stops at the first success) succeeds via the EI branch, `isUser` in the controller evaluates to `true` just as it would for a real user [4](#0-3) .

This means the restriction is broken not merely by "header ordering/precedence confusion" with a forged API token — a valid EI credential *alone* is sufficient to satisfy `isUser == true` and reach the `RunJobV2(ctx, jobID, nil)` call for an arbitrary int32 job ID, which the code comment explicitly says should be forbidden for EIs. The webhook/UUID-based job path that was presumably meant to be the exclusive path for EIs has been removed (`job.ErrJobTypeRemoved` at lines 103-107), leaving the int32 branch as the only viable path and now reachable by EIs too.

### Impact Explanation
An external-initiator-role identity — which was intended to be limited to triggering runs on the (now removed) webhook/UUID job type — can instead trigger arbitrary job runs on any job identified by an int32 ID, exactly the elevated capability the `isUser`/"EIs not allowed" check was meant to prevent. This is an authorization-bypass / role-confusion issue: identity established via one auth method (EI) is silently treated as equivalent to identity established via user session/token, violating the "identity cannot be confused across auth methods" invariant. Impact matches Chainlink's "unauthorized job run" bounty class, since a lower-privileged External Initiator identity can invoke `RunJobV2` on jobs it should not be authorized to run directly by int ID.

### Likelihood Explanation
Requires only valid EI credentials (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` style headers, per `bridges.AuthenticateExternalInitiator`) for a job that has EI-based triggering configured — no forged/stale API token or header-precedence trick is actually needed, since `AuthenticateExternalInitiator` unconditionally sets the same `SessionUserKey` context value used to gate the check. This makes the bypass deterministic and repeatable for any EI credential holder, not merely a corner case dependent on ambiguous header combinations.

### Recommendation
Do not rely on presence-of-`SessionUserKey` (`isUser`) to distinguish real users from EI pseudo-users. Instead, explicitly check `auth.GetAuthenticatedExternalInitiator(c)` (returns `ok == true` only when authenticated via the EI method) and reject the int32-ID path when an EI identity is present, or check `user.Role` combined with an explicit "is this identity synthetic/EI" flag rather than overloading `SessionUserKey` for both real and synthetic identities.

### Proof of Concept
Go handler-level integration test:
1. Register an `ExternalInitiator` with valid access key/secret in the ORM.
2. Build a gin context/request to `POST /v2/jobs/:ID/runs` where `:ID` is a valid int32 job ID, setting only the EI headers (`X-Chainlink-EA-AccessKey`, `X-Chainlink-EA-Secret`) and no session cookie or API token headers.
3. Route the request through `auth.Authenticate(store, auth.AuthenticateBySession, auth.AuthenticateByToken, auth.AuthenticateExternalInitiator)` followed by `PipelineRunsController.Create`.
4. Assert the response is `200 OK` with a pipeline run resource returned (i.e., `RunJobV2` was invoked), demonstrating that `isUser` was `true` and the "EIs not allowed" restriction was bypassed using EI-only credentials.
5. As a control, assert `auth.GetAuthenticatedExternalInitiator(c)` returns `ok == true` for the same request, proving the identity was in fact an EI, not a genuine user, at the time `isUser` was evaluated true.

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

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
