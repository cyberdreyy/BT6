### Title
EI-authenticated requests bypass the "EIs not allowed" check in `PipelineRunsController.Create`, allowing any valid EI credential to trigger arbitrary job runs by job ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`POST /v2/jobs/:ID/runs` is the only run-triggering route reachable via `auth.AuthenticateExternalInitiator` and is wrapped with `auth.RequiresRunRole`, so the role-check itself is present. However, `AuthenticateExternalInitiator` stores a stub `clsessions.User{Role: UserRoleRun}` under the same session key used by real users, which makes `PipelineRunsController.Create`'s `isUser` guard — intended to block EIs from calling this endpoint ("EIs not allowed") — always evaluate true for EI-authenticated calls too, and the handler never validates that the authenticated external initiator is associated with the target job.

### Finding Description
The router registers: [1](#0-0) 

`auth.AuthenticateExternalInitiator` authenticates the EI by access key/secret, then explicitly stores a synthetic run-role user in the same `SessionUserKey` used for genuine session/token users: [2](#0-1) 

`auth.RequiresRunRole` only checks `GetAuthenticatedUser`'s `Role` field, which is satisfied by this synthetic user, so the role wrapper passes for any successfully authenticated EI: [3](#0-2) 

Inside the handler, the code comment states intent to disallow EIs, but the guard used, `auth.GetAuthenticatedUser`, merely checks whether `SessionUserKey` is present — which is true for both real users and EI-triggered stub users — so it cannot actually distinguish an EI caller from a genuine dashboard/API-token user: [4](#0-3) 

As a result, an attacker holding valid credentials for external initiator "A" can call `POST /v2/jobs/:ID/runs` with `:ID` set to any integer job ID in the system (not limited to jobs configured with EI "A" as trigger), and `prc.App.RunJobV2(ctx, jobID, nil)` will execute unconditionally — there is no lookup or comparison of the authenticated `bridges.ExternalInitiator`'s name/ID against the target job's configured external initiator before invoking the run.

### Impact Explanation
This allows cross-tenant/cross-job run triggering: any holder of a valid EI credential (issued for one job) can force execution of pipeline runs on arbitrary other jobs by supplying their integer job ID, without any binding check to the EI that legitimately owns that job. This can be used to trigger pipeline runs (and downstream side effects — e.g., on-chain transactions, VRF fulfillments, other job-specific business logic) for jobs the attacker does not control, corresponding to the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Feasibility is high and reproducible: an attacker needs only a single legitimate EI access key/secret pair for any job (external initiators are commonly distributed to third-party integrations), and knowledge or enumeration of a target job's integer ID (job IDs are sequential/enumerable via `/v2/jobs` index or brute force). No admin/operator access is required — this matches the described unprivileged EI-holder threat model exactly.

### Recommendation
In `PipelineRunsController.Create`, distinguish EI-authenticated requests from real user sessions (e.g., via `auth.GetAuthenticatedExternalInitiator(c)` rather than relying on the shared `SessionUserKey`), and when the caller is an EI, look up the target job's configured external initiator and reject the request unless the authenticated EI's name/ID matches the job's associated external initiator before calling `RunJobV2`.

### Proof of Concept
1. Seed two jobs, Job A bound to `ExternalInitiator("ei-a")` and Job B bound to `ExternalInitiator("ei-b")` (or Job B with no EI association at all), both with integer IDs.
2. Using `ei-a`'s access key/secret headers (`static.ExternalInitiatorAccessKeyHeader` / `static.ExternalInitiatorSecretHeader`), send `POST /v2/jobs/{JobB.ID}/runs`.
3. Assert the request succeeds (`200`/`201`) and `PipelineORM().FindRun` returns a newly created run for Job B, proving `ei-a`'s credentials triggered a run on Job B despite no association between `ei-a` and Job B.
4. Add a handler-level unit test asserting `auth.GetAuthenticatedExternalInitiator(c).Name` (or ID) must equal the job's configured EI name before `RunJobV2` is called; the current code has no such assertion, so the test fails against the existing implementation.

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

**File:** core/web/auth/auth.go (L202-217)
```go
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
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
}
```

**File:** core/web/pipeline_runs_controller.go (L109-128)
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

	jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
}
```
