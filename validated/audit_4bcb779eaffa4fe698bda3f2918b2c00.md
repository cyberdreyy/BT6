### Title
External-initiator credentials bypass the "EIs not allowed" check and can trigger arbitrary job runs via POST /v2/jobs/:ID/runs - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` and `SessionUserKey` in the gin context for any valid EI credential, making `auth.GetAuthenticatedUser(c)` return `true` for EI-authenticated requests exactly as it would for a real user session/token. `PipelineRunsController.Create` uses `isUser := auth.GetAuthenticatedUser(c)` as the sole gate for the comment-stated rule "only users are allowed to run jobs using int IDs - EIs not allowed", so this gate is defeated and any valid EI credential can call `RunJobV2` for an arbitrary integer job ID.

### Finding Description
`AuthenticateExternalInitiator` (core/web/auth/auth.go:119-151) authenticates the EI access-key/secret pair via `bridges.AuthenticateExternalInitiator`, and then unconditionally does: [1](#0-0) 
This sets `SessionUserKey` to a synthetic `clsessions.User{Role: UserRoleRun}` — the *same* context key that `AuthenticateBySession`/`AuthenticateByToken` use for real, logged-in users.

`PipelineRunsController.Create` (core/web/pipeline_runs_controller.go:89-128) is intended to prevent EIs from triggering runs by integer job ID (webhook/EI job type was removed): [2](#0-1) 
The check `_, isUser := auth.GetAuthenticatedUser(c)` cannot distinguish an EI-authenticated request from a real user session/token, because `GetAuthenticatedUser` simply reads `SessionUserKey` (core/web/auth/auth.go:178-187), which is populated identically for both cases. As a result, `isUser` is `true` for any successfully authenticated EI, the guard is bypassed, and `RunJobV2` is invoked with the attacker-supplied `jobID` from the URL path — with no ownership/binding check between the EI credential and the target job at all (the `ExternalInitiator` struct in core/bridges/external_initiator.go has no job/bridge reference field to check against in the first place).

Since role-based middleware (`RequiresRunRole`) only checks `Role != UserRoleView` (core/web/auth/auth.go:200-217), and the synthetic EI user is granted `UserRoleRun`, that middleware also does not block the request.

### Impact Explanation
An attacker holding any single valid external-initiator credential (access key + secret) — a low-privilege credential meant only to signal webhook-style job triggers — can invoke `POST /v2/jobs/{anyJobID}/runs` for **any** job on the node, not just a job it is nominally associated with. This is an unauthorized job run / request-impersonation bypass: it lets an EI-scoped credential act with the equivalent of a "run" role user against arbitrary jobs, potentially triggering pipelines that move funds, submit transactions, or consume rate-limited/paid external data, at times/inputs not intended by the node operator.

### Likelihood Explanation
The only precondition is possession of one valid EI access-key/secret pair, which the code assumes to be a low-privilege, narrowly-scoped credential. The bypass requires no additional privilege escalation — it is a direct consequence of `GetAuthenticatedUser` conflating EI sessions with real user sessions. It is fully repeatable and deterministic (no race conditions or timing dependency).

### Recommendation
Distinguish EI-authenticated requests from real user requests instead of relying on `GetAuthenticatedUser` alone. In `PipelineRunsController.Create`, check `auth.GetAuthenticatedExternalInitiator(c)` explicitly and reject non-nil results (rather than only checking for the presence of a `SessionUserKey`), or stop setting `SessionUserKey` in `AuthenticateExternalInitiator` for endpoints where "no EI" is required and instead grant EI-specific job-run authorization only through a dedicated context key checked against the actual job/bridge the EI is bound to.

### Proof of Concept
1. Handler-level integration test (extends `core/web/pipeline_runs_controller_test.go` style):
   - Create a job (any type, e.g. a simple bridge job) with ID `jobB`.
   - Create an `ExternalInitiator` (EI-A) via the EI creation endpoint, capturing its access key/secret. Do not associate it with `jobB` in any way (no such association field exists).
   - Send `POST /v2/jobs/{jobB.ID}/runs` with headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` set to EI-A's credentials (no session cookie, no API token).
   - Assert: response status is `200 OK` and a `PipelineRun` resource is returned (i.e., `RunJobV2` was actually invoked), demonstrating that the "EIs not allowed" comment/intent in `PipelineRunsController.Create` is not enforced.
2. Unit test on `auth.AuthenticateExternalInitiator` + `auth.GetAuthenticatedUser`: assert that after calling `AuthenticateExternalInitiator(c, store)` with valid EI credentials, `auth.GetAuthenticatedUser(c)` returns `(user, true)` — proving the two authentication origins are indistinguishable at the `SessionUserKey` level.

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
