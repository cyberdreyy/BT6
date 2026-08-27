### Title
External Initiator role check in `PipelineRunsController.Create` fails to bind requests to their own job, allowing any EI to trigger any job by ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` synthesizes a generic `User{Role: UserRoleRun}` and stores it under the same `SessionUserKey` used for real dashboard/API-token users. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the request is allowed to run a job by integer ID, but this check cannot distinguish a real user from an EI-synthesized user, and no code path checks the EI object obtained via `GetAuthenticatedExternalInitiator` against the job being run. As a result, an EI credential holder can trigger any job by numeric ID, not only jobs it legitimately owns.

### Finding Description
The route is registered as `userOrEI` group with `Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession, auth.AuthenticateByToken, auth.AuthenticateExternalInitiator)` followed by `auth.RequiresRunRole(prc.Create)` [1](#0-0) .

When authenticating as an external initiator, `AuthenticateExternalInitiator` sets **both** `SessionExternalInitiatorKey` and `SessionUserKey`, the latter with a synthetic `User{Role: UserRoleRun}`: [2](#0-1) 

`RequiresRunRole` only checks `user.Role != UserRoleView`, so the synthetic EI-user passes trivially: [3](#0-2) 

Inside `PipelineRunsController.Create`, the code attempts to gate int-ID job runs to "users" via `isUser, _ := auth.GetAuthenticatedUser(c)`: [4](#0-3) 

However, `GetAuthenticatedUser` simply reads `SessionUserKey` regardless of how it was populated: [5](#0-4) 

Since `AuthenticateExternalInitiator` also populates `SessionUserKey`, `isUser` evaluates to `true` for an EI-authenticated request exactly the same as for a real user. The code comment "only users are allowed to run jobs using int IDs - EIs not allowed" is therefore not enforced by the actual logic. Once inside the `isUser` branch, `prc.App.RunJobV2(ctx, jobID, nil)` is invoked with the caller-supplied `jobID` with **no association check whatsoever** between the authenticated `ExternalInitiator` (retrievable via `auth.GetAuthenticatedExternalInitiator(c)`) and the target job. The webhook/UUID-based job type that historically tied a job to a specific initiator has been removed (`job.ErrJobTypeRemoved`), and no replacement ownership check was added for the int-ID path.

This confirms the audit hypothesis and broadens it: not only can EI A trigger EI B's job, an EI can trigger **any** job in the node by ID, because no code binds the request to the specific job/initiator pairing.

### Impact Explanation
This is a broken object-level authorization / cross-tenant job-run triggering vulnerability. An attacker holding valid credentials for one external initiator can invoke pipelines and bridges configured for any other job on the node, including bridge tasks with embedded credentials, potentially causing unauthorized fund-moving transactions, unwanted external HTTP calls with node-held bridge secrets, and information disclosure through the returned `pipelineRun` resource containing task-level details of a job the attacker does not own. This matches Chainlink bounty impact classes for unauthorized job run / authorization bypass / cross-user response confusion.

### Likelihood Explanation
Preconditions are minimal: only valid EI access-key/secret pair (the lowest-privilege credential type in this system) is required. The exploit is a single unauthenticated-relative-to-target-job HTTP POST to `/v2/jobs/:ID/runs` with the EI's own credentials and any target job's numeric ID. This is trivially repeatable and requires no special network position, race condition, or timing.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject requests authenticated as an external initiator (check `auth.GetAuthenticatedExternalInitiator(c)` presence, not `GetAuthenticatedUser`), or, if EIs are meant to trigger jobs, restore an explicit job-to-external-initiator binding/ownership check before calling `RunJobV2`. Do not reuse `SessionUserKey`/`GetAuthenticatedUser` to represent EI identity; use a distinct check (e.g., `_, isEI := auth.GetAuthenticatedExternalInitiator(c); if isEI { reject }`) so role checks and identity checks aren't conflated.

### Proof of Concept
Go handler-level integration test plan (using `core/internal/cltest` helpers similar to `core/web/pipeline_runs_controller_test.go`):
1. Create two `ExternalInitiator` records, EI-A and EI-B, via the ORM/test helpers.
2. Create Job-A (associated conceptually with EI-A) and Job-B (associated with EI-B), both non-webhook int-ID jobs.
3. Authenticate an HTTP client as EI-A using `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers matching EI-A's credentials.
4. POST to `/v2/jobs/<Job-B.ID>/runs` using the EI-A client.
5. Assert: response status is `200`/`201` and a `pipelineRun` resource is returned (i.e., `RunJobV2` was invoked and succeeded) — demonstrating EI-A triggered Job-B's run.
6. Additionally assert the returned `pipelineRun` JSON includes task-level info from Job-B's pipeline (e.g., bridge names) not associated with EI-A, showing cross-tenant data exposure.
7. Compare against expected correct behavior: the request should be rejected with `401/403` because EI-A is not authorized to trigger Job-B.

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
