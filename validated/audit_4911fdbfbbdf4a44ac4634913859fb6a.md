### Title
External-initiator credential holders bypass the "only users may run int-ID jobs" restriction in `PipelineRunsController.Create` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the caller is allowed to trigger an integer-ID job run, explicitly excluding External Initiators (EIs) per its own comment. However, `AuthenticateExternalInitiator` in `core/web/auth/auth.go` also sets the `SessionUserKey` context value (a synthetic `User{Role: UserRoleRun}`), so `GetAuthenticatedUser` returns `ok=true` for EI-authenticated requests too, silently defeating the intended EI exclusion.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered under the `userOrEI` group, which authenticates via `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession` and then applies `auth.RequiresRunRole`: [1](#0-0) 

Inside `AuthenticateExternalInitiator`, upon successful EI credential validation, the code sets **both** `SessionExternalInitiatorKey` and `SessionUserKey` (a fabricated `User` with `Role: clsessions.UserRoleRun`): [2](#0-1) 

`GetAuthenticatedUser` simply checks for presence of `SessionUserKey` in the gin context and type-asserts it: [3](#0-2) 

In `PipelineRunsController.Create`, the code explicitly intends to restrict integer-ID job runs to real users, per its own comment, but relies solely on `GetAuthenticatedUser`'s boolean: [4](#0-3) 

Because `AuthenticateExternalInitiator` populates `SessionUserKey` with a synthetic user object, `isUser` evaluates to `true` for a request authenticated purely via an EI's `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers — there is no code path that distinguishes "real user" from "EI pretending to be a user." The `RequiresRunRole` wrapper on the route also passes trivially since the synthetic user's `Role` is `UserRoleRun`. As a result, an EI credential — which the code comment says should never be allowed to run int-ID jobs — can successfully call `prc.App.RunJobV2(ctx, jobID, nil)` for any integer job ID.

### Impact Explanation
This is an authorization/authentication-method-isolation bypass: a credential class (External Initiator) that is architecturally meant to be restricted from directly triggering arbitrary integer-ID job runs (webhook/UUID-based external triggering was the intended EI path, and that job type has since been removed) can instead invoke `RunJobV2` for any job by its integer ID, exactly the action the code explicitly tries to forbid. This maps to Chainlink's "unauthorized job run" impact class — an EI holder can trigger runs (and any side effects such as external requests, transactions, or fund-moving pipeline tasks) of jobs it was not meant to access via this endpoint.

### Likelihood Explanation
Exploitation requires only possession of a valid EI credential (`AccessKey`/`Secret`), which is the class of "restricted... external-initiator credential holder" explicitly listed as an in-scope unprivileged attacker. No admin/edit-role action is needed by the attacker beyond already holding EI credentials (which could have been issued for a narrow, legitimate purpose by an admin). The bypass is deterministic and fully reproducible: any int-ID `/v2/jobs/:ID/runs` POST authenticated with valid EI headers succeeds identically to a session/token-authenticated request, since the code makes no distinction between the two once `SessionUserKey` is set.

### Recommendation
In `PipelineRunsController.Create`, do not rely solely on `GetAuthenticatedUser` presence to gate integer-ID runs. Explicitly check `auth.GetAuthenticatedExternalInitiator(c)` and reject the request if an EI is present, or stop `AuthenticateExternalInitiator` from also populating `SessionUserKey` and instead have `RequiresRunRole` and any other role-gated handlers check for either a real user or an EI, while `PipelineRunsController.Create` specifically checks that no EI object is set in context before allowing integer-ID job runs.

### Proof of Concept
Go handler-level integration test plan (in `core/web/pipeline_runs_controller_test.go`):
1. Start a `TestApplication` with `ExternalInitiatorsEnabled = true` and create an int-ID job (e.g., an OCR/direct-request job) via an edit-role/admin session using `CreateJobViaWeb2`.
2. Create an External Initiator via `POST /v2/external_initiators` (using `cltest.CreateExternalInitiatorViaWeb`), capturing its `AccessKey`/`Secret`.
3. Build a plain `http.Client` request to `POST /v2/jobs/<int-job-id>/runs` setting headers `X-Chainlink-EA-AccessKey` and `X-Chainlink-EA-Secret` to the EI's credentials (no session cookie, no API token).
4. Assert the response status is `200 OK` and a `pipelineRun` resource is returned (demonstrating the run succeeded) — expected/intended behavior per the code comment would be `422 Unprocessable Entity` ("bad job ID") since EIs should not run int-ID jobs.
5. As a control, add a unit test for `auth.GetAuthenticatedUser` invoked after `AuthenticateExternalInitiator` runs on a `gin.Context`, asserting `ok == true`, to directly demonstrate that EI authentication satisfies the "isUser" check used in `PipelineRunsController.Create`.

### Citations

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
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
