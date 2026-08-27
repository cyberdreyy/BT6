### Title
External-initiator credentials satisfy the "isUser" check and can trigger job runs for arbitrary int-ID jobs via `/v2/jobs/:ID/runs` - ([File: core/web/pipeline_runs_controller.go], [File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` stores a synthetic `clsessions.User{Role: UserRoleRun}` under the same `SessionUserKey` used by real session/token authentication. [1](#0-0)  `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` merely to test "is this a user" and, if so, runs any int-ID job with no ownership/binding check to the caller's identity. [2](#0-1)  Because the EI-authenticated request also populates `SessionUserKey`, the comment's intent ("only users are allowed to run jobs using int IDs - EIs not allowed") is not actually enforced, and an EI-only credential holder can run any job by numeric ID regardless of which bridge/job that EI was registered against.

### Finding Description
`Authenticate(store, AuthenticateBySession, AuthenticateByToken, AuthenticateExternalInitiator)` tries each method in order and stops at the first success. [3](#0-2)  When only EI credentials are supplied, `AuthenticateExternalInitiator` validates the EI's access key/secret and then sets both `SessionExternalInitiatorKey` and, critically, `SessionUserKey` to a fabricated `UserRoleRun` user object:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [1](#0-0) 

`PipelineRunsController.Create` then does:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [2](#0-1) 

`GetAuthenticatedUser` only checks whether `SessionUserKey` exists in the gin context and type-asserts it to `*clsessions.User` — it cannot distinguish a real session/API-token user from the synthetic `UserRoleRun` user injected by `AuthenticateExternalInitiator`. [4](#0-3)  Consequently `isUser` is `true` for EI-only authenticated requests, defeating the developer's explicit intent expressed in the code comment. The handler then calls `prc.App.RunJobV2(ctx, jobID, nil)` for whatever `jobID` is supplied in the URL, with no check that the caller's `ExternalInitiator` (retrievable via `GetAuthenticatedExternalInitiator`) is actually associated with that job's bridge/webhook spec.

Note: The webhook (UUID-keyed) job type has been removed and is explicitly rejected earlier in `Create` (`uuid.Parse(idStr)` branch returns `ErrJobTypeRemoved`). [5](#0-4)  This means the int-ID path is reachable for any job with a numeric ID (e.g. OCR/OCR2/direct-request jobs), and since there is no per-job EI binding check at all, an attacker holding only EI credentials for one bridge can invoke `RunJobV2` against any other job ID on the node.

### Impact Explanation
This is a request-binding / authorization bypass: a credential scoped only to triggering a specific external-initiator/bridge job effectively gains the `UserRoleRun` role globally and can trigger pipeline runs for arbitrary jobs by integer ID, not just the job it was registered against. This maps to unauthorized job execution — potential unintended fund movement (for DR/keeper-style jobs that dispatch on-chain transactions) or resource abuse/DoS by flooding arbitrary job pipelines with runs.

### Likelihood Explanation
The only precondition is possessing valid EI access-key/secret (the minimal, lowest-privilege credential type — obtainable by anyone the operator has granted EI registration to, e.g. for a single external adapter integration). No user session, API token, or elevated role is required. The exploit is a single unauthenticated-looking HTTP POST with EI headers to a job ID the attacker was never authorized for, and is fully repeatable.

### Recommendation
In `PipelineRunsController.Create`, do not rely on `GetAuthenticatedUser` alone to gate int-ID runs. Explicitly check `GetAuthenticatedExternalInitiator(c)` and reject (or scope) requests where an EI is present, or verify the job's bridge/webhook configuration is bound to that specific `ExternalInitiator` before calling `RunJobV2`. Alternatively, stop `AuthenticateExternalInitiator` from writing into the shared `SessionUserKey`; use a distinct context key so downstream role checks can't conflate EI-derived pseudo-users with real authenticated users.

### Proof of Concept
Handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go`):
1. Start an app with `ExternalInitiatorsEnabled = true`, insert two independent jobs: `jobA` (bound conceptually to bridge/EI "attacker-ei") and `jobB` (an OCR job with int ID, unrelated to the EI), e.g. reuse `setupPipelineRunsControllerTests` to create `jobB`.
2. Register an external initiator via `POST /v2/external_initiators` (`cltest.CreateExternalInitiatorViaWeb`) to obtain `AccessKey`/`Secret` scoped conceptually to `jobA`.
3. Send `POST /v2/jobs/{jobB.ID}/runs` (int ID) using only headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`), with no session cookie or API token, via `cltest.UnauthenticatedPost`.
4. Assert the response is `201 Created` / `200 OK` with a `pipelineRun` resource for `jobB`, and assert a new row appears in `pipeline_runs` for `jobB.ID` — demonstrating `RunJobV2` executed for a job the EI was never registered against, confirming the `isUser` check in `pipeline_runs_controller.go` fails to distinguish EI-derived synthetic users from real ones.

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

**File:** core/web/pipeline_runs_controller.go (L101-107)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
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
