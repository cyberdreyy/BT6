### Title
External Initiator credentials produce an indistinguishable full "run"-role User session, bypassing the explicit EI-exclusion check in `PipelineRunsController.Create` - ([File: core/web/auth/auth.go] / [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` unconditionally sets `SessionUserKey` to `&clsessions.User{Role: clsessions.UserRoleRun}` in addition to `SessionExternalInitiatorKey`, making an EI-authenticated request indistinguishable from a real run-role user session via `auth.GetAuthenticatedUser`. `PipelineRunsController.Create` (handling `POST /v2/jobs/:ID/runs`) explicitly tries to block EIs from running int-ID jobs using the comment "only users are allowed to run jobs using int IDs - EIs not allowed", but the guard it uses (`isUser, _ := auth.GetAuthenticatedUser(c)`) returns `true` for EI-authenticated requests too, so the intended restriction is ineffective.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) validates the EI access-key/secret against `bridges.ExternalInitiator`, then does: [1](#0-0) 
This sets both `SessionExternalInitiatorKey` and `SessionUserKey`, the latter with `Role: UserRoleRun`.

`GetAuthenticatedUser` (`core/web/auth/auth.go:178-187`) simply reads `SessionUserKey` and returns `(*User, true)` — it has no way to tell whether that user object originated from a real session/token or was synthesized for an EI.

In `PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-127`), the code attempts to enforce that EIs cannot trigger runs via integer job IDs: [2](#0-1) 
The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," but `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which — due to the root cause above — returns `true` for a request authenticated purely via `AuthenticateExternalInitiator`. There is no call to `GetAuthenticatedExternalInitiator` to detect and reject EI-originated sessions. Consequently, an attacker holding only an `ExternalInitiator` access-key/secret pair can call `POST /v2/jobs/:ID/runs` with an integer job ID and successfully invoke `prc.App.RunJobV2(ctx, jobID, nil)`, exactly the action the code comment says should be denied to EIs.

This also generalizes to any `RequiresRunRole`-gated endpoint reachable through a router group where `AuthenticateExternalInitiator` is one of the configured `authMethod`s, since `RequiresRunRole` only checks `user.Role != UserRoleView`, which the synthesized EI user satisfies.

### Impact Explanation
An attacker who has obtained (or otherwise possesses) EI credentials — a lower-trust credential intended only to trigger webhook job runs — can escalate to executing pipeline runs on arbitrary jobs by integer ID via `/v2/jobs/:ID/runs`, and potentially any other run-role-gated endpoint sharing the same auth-method stack. This is an authorization/role-boundary bypass leading to unauthorized pipeline execution, matching Chainlink's "unauthorized job run" impact class.

### Likelihood Explanation
Exploitability requires only a valid EI access key/secret (a credential class explicitly scoped for triggering webhook jobs, not general job execution) and knowledge/guessing of an integer job ID. No operator or admin access is needed. The flaw is deterministic and repeatable given valid EI credentials on any node route configured with `Authenticate(store, AuthenticateExternalInitiator)` (and possibly chained with other auth methods) in front of `RequiresRunRole`-gated handlers or the `PipelineRunsController.Create` route.

### Recommendation
Distinguish EI-derived sessions from genuine user sessions instead of overloading `SessionUserKey`. Options:
- Do not set `SessionUserKey` in `AuthenticateExternalInitiator`; instead have `RequiresRunRole` and other role checks explicitly also accept an EI-only session (via `GetAuthenticatedExternalInitiator`) only for the specific webhook-run endpoint, not generically.
- In `PipelineRunsController.Create`, replace the `isUser` check with an explicit rejection: `if _, isEI := auth.GetAuthenticatedExternalInitiator(c); isEI { reject }`, rather than relying on the absence of `GetAuthenticatedUser`.
- Add a marker field (e.g., `IsExternalInitiator bool`) to the synthesized `User` object, and have `RequiresRunRole`/controllers checking for legitimate "user" callers explicitly reject that marker where EI access should be scoped to webhook-run only.

### Proof of Concept
Go handler-level integration test:
1. Set up a fake `Authenticator` store whose `FindExternalInitiator` returns a valid `bridges.ExternalInitiator` matching supplied `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers (mirroring `external_initiators_controller_test.go` fixtures).
2. Build a gin router: `r.POST("/v2/jobs/:ID/runs", auth.Authenticate(store, auth.AuthenticateExternalInitiator), pipelineRunsController.Create)`.
3. Send `POST /v2/jobs/123/runs` with valid EI headers and no session cookie/API token.
4. Mock `app.RunJobV2` to return a run ID.
5. Assert: response status is `200 OK` and `RunJobV2` was invoked — i.e., the "EIs not allowed" guard did not fire, despite the caller only possessing EI credentials, confirming that `isUser := auth.GetAuthenticatedUser(c)` incorrectly evaluates to `true` for an EI-only session.
6. As a secondary assertion, add a handler wrapped in `auth.RequiresRunRole` (unrelated to webhook jobs) behind the same auth chain, repeat the same EI request, and assert it returns `200` instead of `401/403`, confirming the broader role-boundary bypass.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
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
