### Title
EI-authenticated requests are misidentified as `isUser` due to shared `SessionUserKey`, bypassing the "EIs not allowed" guard in `PipelineRunsController.Create` - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` and `SessionUserKey` (to a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` with empty `Email`), so any code that calls `auth.GetAuthenticatedUser(c)` to test "is this a user session" cannot distinguish an EI caller from a real session/token authenticated run-role user. `PipelineRunsController.Create` explicitly relies on this distinction ("only users are allowed to run jobs using int IDs - EIs not allowed") but the check is defeated because EI callers also satisfy `isUser == true`.

### Finding Description
In `core/web/auth/auth.go`, `AuthenticateExternalInitiator` (lines 119-151) authenticates the caller using EI access-key/secret headers and, on success, does: [1](#0-0) 
This sets `SessionUserKey` to a blank `clsessions.User{Role: UserRoleRun}` (empty `Email`, empty `ID`) in addition to `SessionExternalInitiatorKey`.

`GetAuthenticatedUser` simply reads `SessionUserKey` and returns `ok=true` if anything of type `*clsessions.User` is present: [2](#0-1) 
It has no way to signal "this identity actually came from EI auth, not a real user."

The route `POST /v2/jobs/:ID/runs` is registered in `core/web/router.go` under a middleware chain that tries EI, token, and session auth in sequence: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code explicitly tries to gate integer-ID job runs to "users" only, excluding EIs, using `GetAuthenticatedUser`: [4](#0-3) 
Because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `isUser` evaluates to `true` for a caller that authenticated purely via EI credentials, negating the intended restriction stated in the code comment. An attacker holding only EI access-key/secret (no session, no API token) can therefore invoke `RunJobV2` via an integer job ID path that the comment says should be reserved for real (named/session) users.

Separately, this also creates the ambiguous-identity condition described in the question: the `*clsessions.User` object attached to context for any/all EI callers is a blank struct (`Role: UserRoleRun`, `Email: ""`, `ID: ""`), indistinguishable between different external initiators. Any downstream code (audit logging, ownership checks) that reads this `User` via `GetAuthenticatedUser` cannot attribute the action to a specific EI identity — the real EI identity is only available via the separate `GetAuthenticatedExternalInitiator` accessor, and only if a handler explicitly checks it. `PipelineRunsController.Create` does not consult `GetAuthenticatedExternalInitiator` at all, so it cannot tell one EI apart from another, nor distinguish an EI from a "real" run-role user with an unset email (there is no other code path producing such a user in the reviewed files, but the object shapes are structurally identical and rely purely on convention, not a type/field marker).

### Impact Explanation
This is an authorization-bypass / access-control-confusion issue (Chainlink bounty class: authentication/authorization bypass). Its concrete effect in the reviewed code is: an attacker who possesses only an External Initiator's access-key/secret (a low-privilege credential, not admin/session) can trigger job runs via the integer-ID path in `PipelineRunsController.Create`, a path the code comments indicate should be closed to EIs. Additionally, any handler/audit-log code that trusts `GetAuthenticatedUser`'s returned struct as a reliable, distinguishable user identity will conflate all EI callers into a single blank identity, undermining per-actor accountability for that endpoint.

### Likelihood Explanation
Preconditions are minimal: attacker needs only a valid EI access-key/secret pair (an unprivileged EI credential, matching the allowed attacker profile) and knowledge of a target job's integer ID. No admin, session, or API token is required. The route is reachable directly via `POST /v2/jobs/:ID/runs`, which is wired to accept `AuthenticateExternalInitiator` as the first method in the chain. The bug is deterministic and repeatable — it will trigger on every request authenticated purely via EI credentials that hits `PipelineRunsController.Create` with a numeric `:ID`.

### Recommendation
Do not have `AuthenticateExternalInitiator` populate `SessionUserKey`; instead, provide a distinct signal to downstream handlers that the caller is an EI (e.g. only setting `SessionExternalInitiatorKey`, and have `RequiresRunRole`/other role checks consult a unified "principal" type that explicitly tags the auth method and identity, such as an EI name or ID). `PipelineRunsController.Create`'s check should test `GetAuthenticatedExternalInitiator` (i.e., "is this an EI") and reject the integer-ID path if an EI principal is present, rather than inferring "user-ness" from the accidental presence of a `*clsessions.User` object. Any audit logging for job runs should also log the concrete authenticated principal (EI name or user email/ID) rather than the ambiguous blank `User` struct.

### Proof of Concept
Go handler-level integration test (extends existing tests in `core/web/auth/auth_test.go` and `core/web/pipeline_runs_controller_test.go` patterns):
1. Set up a test router with the `userOrEI` group exactly as in `v2Routes` (`auth.Authenticate(provider, auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)`) wrapping `PipelineRunsController.Create` via `auth.RequiresRunRole`.
2. Seed a valid `bridges.ExternalInitiator` in the test DB/mock `Authenticator`, with no session/token created for the caller.
3. Send `POST /v2/jobs/123/runs` with only the `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set (no session cookie, no `X-API-KEY`/`X-API-SECRET`).
4. Assert:
   - The request passes `Authenticate` (EI auth succeeds).
   - Inside the handler, `auth.GetAuthenticatedUser(c)` returns `ok == true` (demonstrating the bypass of the "EIs not allowed" comment).
   - `RunJobV2` is invoked and a `202`/`200` pipeline-run response is returned, rather than the expected `422 "bad job ID"` for an EI caller.
   - `auth.GetAuthenticatedExternalInitiator(c)` also returns `ok == true`, showing the identity object exposed to `GetAuthenticatedUser` cannot be used to distinguish this EI from another EI or from a legitimately blank-email user, since both would produce `Role: UserRoleRun, Email: ""`.

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
