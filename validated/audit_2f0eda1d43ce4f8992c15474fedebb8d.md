### Title
EI credentials satisfy the `isUser` check and can trigger ANY non-webhook job by integer ID via `RunJobV2` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` gates the integer-job-ID run path with `auth.GetAuthenticatedUser(c)` and a comment claiming "EIs not allowed", but `AuthenticateExternalInitiator` populates the exact same `SessionUserKey` context value with a `*clsessions.User{Role: UserRoleRun}`, so `isUser` is `true` for EI-authenticated requests too. As a result, any valid External-Initiator credential can invoke `RunJobV2` against an arbitrary job ID that has nothing to do with the EI's own (now-removed) webhook job.

### Finding Description
In `PipelineRunsController.Create`, the UUID branch only rejects webhook-style job IDs; the int32 branch is guarded solely by: [1](#0-0) 

The comment states "only users are allowed to run jobs using int IDs - EIs not allowed," implying `isUser` should be `false` for EI-authenticated requests. However, `AuthenticateExternalInitiator` sets the identical session key used by real user auth: [2](#0-1) 

`GetAuthenticatedUser` performs only a type assertion on that key, with no way to distinguish a real authenticated user from an EI-derived pseudo-user: [3](#0-2) 

Consequently, an EI credential holder (bound to "job A" via `bridges.ExternalInitiator`) can send `POST /v2/jobs/<jobB-int-ID>/runs` where job B is any other job (OCR, cron, VRF, etc.) known by integer ID, and the handler will call `prc.App.RunJobV2(ctx, jobID, nil)` unconditionally — there is no check tying the EI identity to the specific job ID requested. Note that since `job.Webhook` creation is now hard-rejected in `orm.CreateJob` (`ErrJobTypeRemoved`), the EI's only legitimate historical use case (triggering its own bound webhook job) is gone, but the EI-auth middleware still grants it a full run-role pseudo-user session usable against this endpoint for any other job type. [4](#0-3) 

### Impact Explanation
This is a role/authorization bypass allowing a lower-trust credential (External Initiator access key/secret) to trigger job runs it does not own, for arbitrary job types (not just webhook), matching the "unauthorized job run" bounty impact class. Since a triggered pipeline run can include tasks that submit on-chain transactions or write bridge responses, an attacker holding only EI credentials could cause unintended job executions, potential fund movement (e.g., a job that submits a transaction task), or resource exhaustion by repeatedly triggering another tenant's/job-owner's job.

### Likelihood Explanation
Preconditions are minimal: attacker only needs one valid EI credential (access key + secret) for any External Initiator registered on the node — this is explicitly a lower-privilege credential type distinct from a full user session or API token. The attacker also needs the target job's integer ID, which is sequential/guessable or discoverable via other means. The exploit requires no additional privilege, no operator/admin access, and is fully repeatable (each POST triggers a new run).

### Recommendation
Do not rely on the shared `SessionUserKey`/`GetAuthenticatedUser` check to gate this endpoint. Explicitly detect and reject EI-authenticated requests (e.g., via `auth.GetAuthenticatedExternalInitiator(c)` returning `ok == true`, or by using a distinct context key/type for the EI pseudo-user instead of reusing `clsessions.User`) before entering the int32-ID `RunJobV2` branch. Given that webhook jobs are permanently removed, the safest fix is to unconditionally reject any request authenticated via `AuthenticateExternalInitiator` in `PipelineRunsController.Create`, regardless of ID format.

### Proof of Concept
Add an integration test in `core/web/pipeline_runs_controller_test.go`:
1. Start an app, create an External Initiator `eiA` via `cltest.CreateExternalInitiatorViaWeb`, obtaining `AccessKey`/`Secret`.
2. Create a separate non-webhook job B (e.g., an OCR or cron job) via `app.AddJobV2`, capturing its int32 `jb.ID`.
3. Issue `POST /v2/jobs/<jobB.ID>/runs` using `cltest.UnauthenticatedPost` with headers `static.ExternalInitiatorAccessKeyHeader`/`static.ExternalInitiatorSecretHeader` set to `eiA`'s credentials (no session cookie, no API token).
4. Assert (expected/fixed behavior): response status is `401`/`403` and `pipeline_runs` count for job B does not increase.
5. Currently (pre-fix): assert the request returns `200`/`201` with a valid `pipelineRun` resource and `pipeline_runs` row for job B, proving an EI credential unrelated to job B can trigger it.

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

**File:** core/services/job/orm.go (L174-179)
```go
func (o *orm) CreateJob(ctx context.Context, jb *Job) error {
	// Permanently removed job types: reject all new submissions regardless of
	// which code path reaches here (REST API, GraphQL, feeds manager, etc.).
	if jb.Type == DirectRequest || jb.Type == FluxMonitor || jb.Type == Webhook {
		return fmt.Errorf("cannot create job of type %q: %w", jb.Type, ErrJobTypeRemoved)
	}
```
