### Title
EI credential incorrectly satisfies `isUser` check, bypassing "EIs not allowed" restriction and enabling any job to be run via `RunJobV2` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` intends to only allow authenticated Users (not External Initiators) to trigger a job run by integer job ID, per its own comment. However, the `isUser` gate is implemented via `auth.GetAuthenticatedUser(c)`, which returns `true` for EI-authenticated requests as well, because `AuthenticateExternalInitiator` stores a synthetic `&clsessions.User{Role: UserRoleRun}` under the same `SessionUserKey`. This lets any valid EI credential holder call `prc.App.RunJobV2(ctx, jobID, nil)` for an arbitrary job ID.

### Finding Description
In `core/web/pipeline_runs_controller.go`, `Create` does: [1](#0-0) 

The gating logic explicitly states "only users are allowed to run jobs using int IDs - EIs not allowed," but the check `_, isUser := auth.GetAuthenticatedUser(c)` only tests whether *something* was stored under `SessionUserKey` — it does not check whether the authentication method was a genuine user session/token versus an EI credential.

In `core/web/auth/auth.go`, `AuthenticateExternalInitiator` sets the exact same session key used for real users: [2](#0-1) 

`GetAuthenticatedUser` simply type-asserts on `SessionUserKey` and returns `true` if present, with no way to distinguish the EI-injected synthetic user from a genuine authenticated user: [3](#0-2) 

Because of this, an EI credential holder passing `AuthenticateExternalInitiator` middleware will have `isUser == true` in `Create`, satisfy the branch, and reach `prc.App.RunJobV2(ctx, jobID, nil)` with **any attacker-supplied `jobID`** — `RunJobV2` takes only a job ID and has no parameter or internal check tying the run to the specific EI (or User) that authenticated the request, so there is no ownership/ACL check preventing an EI from triggering a run for a job it does not own.

### Impact Explanation
This is an authorization/isolation bypass: an entity holding only an External Initiator credential (a low-privilege, narrowly-scoped credential meant only to trigger its own associated webhook job) can force execution of **any** job on the node by supplying an arbitrary integer job ID, including jobs it has no relationship to. This matches the "unauthorized job run" impact class — cross-job (and by extension cross-consumer) triggering of pipeline runs, potential unwanted on-chain transactions/writes, and consumption of node resources/task execution tied to unrelated jobs.

### Likelihood Explanation
Preconditions: possession of any valid EI access key/secret pair (the minimal, lowest-privilege credential type in the system) and knowledge/guessing of a target job's integer ID (which are typically small sequential values, easily enumerable). No user session, no admin/edit role, and no relationship to the target job is required. This is a straightforward, repeatable HTTP POST against `/v2/jobs/:ID/runs` with EI auth headers instead of a session/API token, so likelihood is high given any working EI credential.

### Recommendation
Distinguish EI-derived sessions from genuine user sessions rather than relying on the shared `SessionUserKey`. Options:
- Check `auth.GetAuthenticatedExternalInitiator(c)` first and reject (403) if an EI context is present, regardless of the synthetic user object.
- Or avoid overloading `SessionUserKey` for EIs; add a distinct flag/session key (e.g., `IsExternalInitiator bool`) set only by `AuthenticateExternalInitiator`, and check that flag explicitly in `PipelineRunsController.Create` before allowing the int-ID `RunJobV2` path.
- Additionally, if job-scoped EI runs are still needed, enforce that the run request's job ID matches a job the EI is authorized for.

### Proof of Concept
Handler-level integration test plan (Go, using `core/web` test harness similar to `pipeline_runs_controller_test.go`):
1. Create two jobs, `jobA` (int ID `1`) and `jobB` (int ID `2`), neither belonging to any EI-specific ownership record.
2. Create an External Initiator credential `EI1` via `bridges.NewExternalInitiator`, register it, and construct a request to `POST /v2/jobs/2/runs` using headers `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader/SecretHeader`) instead of session/API-key headers.
3. Assert current (buggy) behavior: response is `200 OK` and `App.RunJobV2` is invoked with `jobID=2`, proving the EI can trigger a run for a job unrelated to it.
4. Expected assertion after fix: response should be `403 Forbidden` (or equivalent auth error), and `RunJobV2` must not be called, when the caller authenticated via `AuthenticateExternalInitiator` and attempts to hit the integer-ID run endpoint.
5. Table-test variant: iterate over several job IDs not associated with the EI and assert all are rejected identically, confirming no bypass exists across arbitrary IDs.

### Citations

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

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
