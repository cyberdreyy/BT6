### Title
GetAuthenticatedUser cannot distinguish EI-issued sessions from real user sessions, defeating the "EIs not allowed" restriction in PipelineRunsController.Create - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` stores a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` under the exact same `SessionUserKey` used by real user authentication (`AuthenticateBySession`/`AuthenticateByToken`). `GetAuthenticatedUser` simply checks for the presence and type of that key, so it cannot tell a genuine user session from an EI-derived one. `PipelineRunsController.Create` relies on `isUser := ok` from `GetAuthenticatedUser` to enforce "only users are allowed to run jobs using int IDs - EIs not allowed", but that check is satisfied identically by both cases.

### Finding Description
`AuthenticateExternalInitiator` (core/web/auth/auth.go:119-151) authenticates via EI access key/secret and, instead of only setting `SessionExternalInitiatorKey`, also injects a fabricated `User` object into `SessionUserKey`: [1](#0-0) 

`GetAuthenticatedUser` (core/web/auth/auth.go:178-187) has no way to know which `authMethod` populated `SessionUserKey` — it just does a context `Get` + type assertion: [2](#0-1) 

`PipelineRunsController.Create` (core/web/pipeline_runs_controller.go:109-125) uses exactly this ambiguous signal to gate int-ID job runs: [3](#0-2) 

The comment "only users are allowed to run jobs using int IDs - EIs not allowed" documents an intended authorization boundary between EI credentials and human/API-token users, but the implementation is incapable of enforcing it: `isUser` is `true` for any request that reaches this handler with a populated `SessionUserKey`, and `AuthenticateExternalInitiator` always populates it (with `UserRoleRun`, which also passes `RequiresRunRole`). Whether this is practically exploitable at the given endpoint (`POST /v2/jobs/:ID/runs`) depends on whether the route's authentication chain in `core/web/router.go` includes `auth.AuthenticateExternalInitiator` alongside `AuthenticateBySession`/`AuthenticateByToken` for that route — I was not able to confirm the exact route wiring for this endpoint in the time available. If that route's middleware chain includes `AuthenticateExternalInitiator`, an EI credential holder can trigger arbitrary int-ID job runs despite the code's explicit intent to forbid it.

### Impact Explanation
If the `POST /v2/jobs/:ID/runs` route is wired to accept `AuthenticateExternalInitiator` as one of its auth methods, an External Initiator credential (a lower-trust integration credential, not a full user/API-token) can trigger arbitrary pipeline job runs by int32 ID — an unauthorized job run/privilege escalation matching the "unauthorized job run" bounty impact class. Regardless of routing confirmation, the root-cause bug is real and confirmed: `GetAuthenticatedUser`/`isUser` provides no origin/authentication-method separation, so any code relying on "isUser == real user, not EI" is unsound.

### Likelihood Explanation
Preconditions are minimal: only a valid EI access key/secret pair (no admin, no user account, no API token) is required. If the route accepts the EI auth method, exploitation is a single authenticated POST request — fully repeatable and requires no race condition or special timing.

### Recommendation
Do not overload `SessionUserKey` to represent EI identity. Introduce a distinct sentinel/type (or a boolean flag on the context, e.g. `SessionIsExternalInitiator`) set only by `AuthenticateExternalInitiator`, and have `PipelineRunsController.Create` explicitly check `_, isEI := auth.GetAuthenticatedExternalInitiator(c); if isEI { reject }` instead of inferring "not EI" from the mere presence of a `SessionUserKey` value. Alternatively, have `GetAuthenticatedUser` return `false` when the session was established via `AuthenticateExternalInitiator` (i.e., never populate `SessionUserKey` for EI-authenticated requests) and have EI-only endpoints check `GetAuthenticatedExternalInitiator` explicitly.

### Proof of Concept
Handler-level integration test:
1. Set up a `gin.Context` and call `AuthenticateExternalInitiator` with valid EI credentials against a stubbed `Authenticator` (mirroring `core/web/auth/auth_test.go` patterns).
2. Assert `auth.GetAuthenticatedUser(c)` returns `(_, true)` — proving the EI session is indistinguishable from a real user session.
3. Invoke `PipelineRunsController.Create` with `c.Param("ID")` set to a valid int32 job ID string.
4. Assert that `prc.App.RunJobV2` is invoked and a `pipelineRun` JSON response is returned (HTTP 200), instead of the expected `bad job ID` / unauthorized rejection — demonstrating the "EIs not allowed" comment is not enforced by the code.
5. (Separately, to fully confirm exploitability) verify in `core/web/router.go` whether the `/v2/jobs/:ID/runs` POST route chain includes `auth.AuthenticateExternalInitiator`; if so, the handler-level PoC above translates directly into a live HTTP exploit.

### Citations

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
