### Title
External-initiator synthetic user bypasses `PipelineRunsController.Create`'s "EIs not allowed" check and any `RequiresRunRole`-gated route because it carries no job/bridge identity binding - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets `SessionUserKey` to a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` with no `Email`/`ID`, making it indistinguishable from a real, session/token-authenticated user to any code that only calls `GetAuthenticatedUser`/checks `.Role`. Both `auth.RequiresRunRole` and `PipelineRunsController.Create`'s local `isUser` check rely solely on `GetAuthenticatedUser` succeeding, so an EI credential is treated exactly like a role="run" user with no binding to any specific job or bridge.

### Finding Description
`AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-151`) validates the EI's access key/secret against `bridges.ExternalInitiator`, then does: [1](#0-0) 
This sets `SessionUserKey` to a brand-new `User` struct with only `Role` populated - no `Email`, no `ID`, and critically no reference back to `ei.Name`/`ei.ID` or any bridge/job it is scoped to.

`RequiresRunRole` (`core/web/auth/auth.go:202-217`) only checks: [2](#0-1) 
i.e., it rejects `UserRoleView` and allows everything else - it has no concept of "this identity is only authorized for job X". Any handler wrapped in `RequiresRunRole` is therefore reachable by any EI credential with no further check tying the EI to the specific resource being acted on.

`PipelineRunsController.Create` (`core/web/pipeline_runs_controller.go:89-128`) attempts to defend against EI-triggered runs via int job IDs with a comment "only users are allowed to run jobs using int IDs - EIs not allowed", implemented as: [3](#0-2) 
`isUser` is derived from `auth.GetAuthenticatedUser(c)`, which returns `(user, true)` for *both* real users and EI-authenticated requests, because `AuthenticateExternalInitiator` also populates `SessionUserKey`. There is no check distinguishing whether the `User` in the gin context originated from a real session/token or from the synthetic EI user, and no check that `jobID` belongs to the EI's own bridge/job. This means the stated intent ("EIs not allowed" for int-ID job runs) is not actually enforced by the code - any identity that reaches this handler with `Role != View` (including the EI synthetic user) can call `prc.App.RunJobV2(ctx, jobID, nil)` for an arbitrary `jobID`, regardless of which bridge/EI it belongs to.

### Impact Explanation
An external-initiator credential - typically scoped to trigger runs only for the webhook job tied to its own bridge - can be leveraged to invoke pipeline runs for **any** job by integer ID once authenticated, including jobs owned by unrelated bridges/EIs, because neither `RequiresRunRole` nor the `Create` handler's `isUser` check binds the caller's identity to a specific job/bridge. This constitutes unauthorized job execution / authorization bypass, matching the "unauthorized job run" bounty impact class, since it lets one integration's low-privilege credential drive execution of arbitrary node pipelines (potential fund movement or unintended external calls if those jobs perform on-chain transactions).

### Likelihood Explanation
Preconditions: the attacker needs only a valid EI access key/secret for *any one* registered external initiator (a low-privilege, non-admin credential) and knowledge/guessability of an integer job ID for a target job. No admin/operator access is required. Given EI credentials are routinely distributed to third-party integrations, and job IDs are small sequential integers, this is a highly feasible and repeatable path once the route is reachable with EI auth headers.

Note: I was not able to conclusively confirm from the available code context whether `/v2/jobs/:ID/runs` is registered under a router group that includes `auth.AuthenticateExternalInitiator` as one of its `authMethod`s (the `v2Routes`/job route registration snippet retrieved did not show the exact middleware chain for this specific route). The comment "EIs not allowed" in `PipelineRunsController.Create` strongly implies EI-authenticated requests do reach this handler in production, but this specific wiring could not be independently verified with the tools available in this session.

### Recommendation
- Bind the EI-derived synthetic user to its owning bridge (e.g., set an identifying field like `ExternalInitiatorID`/`ExternalInitiatorName` on the context user, or keep it as a distinct type) rather than reusing the generic `clsessions.User` struct indistinguishably from real users.
- In `PipelineRunsController.Create`, explicitly check for `auth.GetAuthenticatedExternalInitiator(c)` and reject (not just "isUser" via role) any int-ID run request that originated from EI authentication, or verify that the target job is actually bound to that specific EI's bridge before permitting the run.
- Consider not setting `SessionUserKey` at all for EI-authenticated requests, and instead have `RequiresRunRole`-style checks look at `GetAuthenticatedExternalInitiator` explicitly for EI-scoped routes, so generic role-based middleware never silently authorizes EI callers for endpoints not designed for them.

### Proof of Concept
1. Handler-level Go test in `core/web/pipeline_runs_controller_test.go`:
   - Create two jobs, `jobA` (bound to bridge/EI `ei1`) and `jobB` (bound to bridge/EI `ei2`), each backed by a webhook/bridge task.
   - Register `ei1` and `ei2` via `POST /v2/external_initiators`, capturing their `AccessKey`/`Secret`.
   - Send `POST /v2/jobs/:jobB_intID/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `ei1`'s credentials (not `ei2`'s).
   - Assert: current behavior returns `200`/`201` with a created pipeline run (bug), whereas expected behavior is `401`/`403` since `ei1` has no relation to `jobB`.
2. Unit test on `auth.AuthenticateExternalInitiator` + `PipelineRunsController.Create`: build a `gin.Context`, call `AuthenticateExternalInitiator` with a valid EI token, then call `Create` with an arbitrary int job ID; assert `auth.GetAuthenticatedUser(c)` returns `ok=true` with `Role=UserRoleRun` and no identifying fields, demonstrating that `isUser` cannot differentiate EI callers from real users as the code comment assumes.

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

**File:** core/web/auth/auth.go (L203-216)
```go
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
