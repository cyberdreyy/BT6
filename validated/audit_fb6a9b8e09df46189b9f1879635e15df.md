### Title
External-Initiator credential holders can trigger integer-ID job runs bypassing the intended EI restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` gates integer-ID job runs on `auth.GetAuthenticatedUser(c)` returning `true`, with an explicit comment stating "only users are allowed to run jobs using int IDs - EIs not allowed." However, `AuthenticateExternalInitiator` unconditionally sets `SessionUserKey` to a synthetic `User{Role: UserRoleRun}` on every successful EI authentication, so `isUser` is always `true` for EI-authenticated requests as well — no stale cookie is even needed. This makes the EI-exclusion check dead code, allowing any external-initiator credential holder to run arbitrary jobs by integer ID.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered on the `userOrEI` group with the authenticator chain `AuthenticateExternalInitiator, AuthenticateByToken, AuthenticateBySession`, wrapped by `auth.RequiresRunRole(prc.Create)`: [1](#0-0) 

`Authenticate` tries each method in order and stops at the first one that doesn't return `ErrorAuthFailed`: [2](#0-1) 

`AuthenticateExternalInitiator`, on success, sets **both** `SessionExternalInitiatorKey` and `SessionUserKey` to a synthetic run-role user, purely so `RequiresRunRole` will pass: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code intends to distinguish "real" users from EIs via `auth.GetAuthenticatedUser(c)`: [4](#0-3) 

But `GetAuthenticatedUser` simply reads `SessionUserKey` from context — it cannot distinguish a genuine session/token user from the synthetic user object that `AuthenticateExternalInitiator` itself injected: [5](#0-4) 

Consequently, for any request authenticated solely via valid EI access-key/secret headers, `isUser` in `Create` is `true`, the `idStr` is parsed as an int32, and `prc.App.RunJobV2(ctx, jobID, nil)` is invoked directly — exactly the code path the comment says should be blocked for EIs. No stale session cookie is required to reach this state; the EI auth method alone produces it.

### Impact Explanation
An external-initiator credential (typically scoped/expected to trigger only its own associated webhook job) can instead run **any** job on the node by supplying its integer job ID, bypassing the intended access boundary between EI credentials and full user credentials. This is an authorization bypass allowing unauthorized job runs, matching the "unauthorized job run" bounty impact class. Depending on job types configured on the node (e.g., jobs that move funds, write on-chain transactions, or read sensitive bridge data), this can result in unauthorized state changes or fund movement triggered by a low-privilege EI credential.

### Likelihood Explanation
- Preconditions: attacker needs a valid external-initiator access key/secret pair (a common, lower-trust credential type explicitly listed as in-scope for unprivileged attackers).
- No other role, cookie, or token is needed — the exploit works purely from `AuthenticateExternalInitiator` succeeding.
- The attack is deterministic and repeatable: any integer job ID on the node can be targeted this way.
- Feasibility is high since it only requires knowledge of a job's integer ID, which can often be enumerated (small sequential integers).

### Recommendation
Fix the isUser gate so it reflects the actual authentication method used, not just presence of `SessionUserKey`. Options:
- In `AuthenticateExternalInitiator`, do not set `SessionUserKey`; instead have `RequiresRunRole` accept either a real user or an authenticated external initiator (check `SessionExternalInitiatorKey` too) instead of requiring EI logic to fake a `User`.
- In `PipelineRunsController.Create`, additionally check `auth.GetAuthenticatedExternalInitiator(c)` and explicitly reject the integer-ID path if an EI (not a genuine user) is present, e.g. `if _, isEI := auth.GetAuthenticatedExternalInitiator(c); isEI { return 422 }` before checking `isUser`.

### Proof of Concept
Go handler-level integration test plan:
1. Set up an app with `v2Routes` registered, create an external initiator record and obtain its access key/secret headers.
2. Create a job (e.g., OCR/any non-webhook job) and note its integer `jb.ID`.
3. Send `POST /v2/jobs/{jb.ID}/runs` with only the `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers set (no session cookie, no API token headers).
4. Assert current (buggy) behavior: response is `200 OK` with a `pipelineRun` resource, and `App.RunJobV2` was actually invoked for `jb.ID` — i.e., the EI successfully ran a job it should not be permitted to run by integer ID.
5. Add an assertion that after the fix, the same request returns `422 Unprocessable Entity` (`"bad job ID"` or similar EI-specific rejection) even though headers are valid EI credentials, confirming `isUser`/EI distinction correctly blocks the integer-ID path for EI-only authentication regardless of any session cookie state.

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
