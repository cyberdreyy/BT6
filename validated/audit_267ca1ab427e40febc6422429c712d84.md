### Title
Broken "EIs not allowed" check in PipelineRunsController.Create lets any authenticated External Initiator trigger runs on arbitrary jobs by numeric ID - (File: core/web/pipeline_runs_controller.go)

### Summary
`PipelineRunsController.Create` intends to block external-initiator (EI) credentials from triggering job runs via numeric job IDs (only "users" should be allowed), but the guard it uses (`auth.GetAuthenticatedUser`) cannot distinguish a real user session from an EI session, because `AuthenticateExternalInitiator` also populates the same `SessionUserKey` context value. As a result, any holder of a valid EI AccessKey/Secret pair passes the `isUser` check and can call `prc.App.RunJobV2(ctx, jobID, nil)` for any numeric job ID, without any binding between the EI's identity and the specific job.

### Finding Description
`auth.AuthenticateExternalInitiator` (core/web/auth/auth.go:119-151) authenticates purely on the global `FindExternalInitiator(access_key)` lookup and, on success, unconditionally sets: [1](#0-0) 
this stores a dummy `*clsessions.User{Role: UserRoleRun}` under the exact same `SessionUserKey` that `AuthenticateBySession`/`AuthenticateByToken` use for real user sessions.

`PipelineRunsController.Create` (core/web/pipeline_runs_controller.go) tries to reject EI-authenticated callers from the numeric-ID run-trigger path with a comment stating "only users are allowed to run jobs using int IDs - EIs not allowed": [2](#0-1) 
The check `_, isUser := auth.GetAuthenticatedUser(c)` only tests whether *any* object exists under `SessionUserKey` and type-asserts to `*clsessions.User` — it does not check `SessionExternalInitiatorKey`/whether the caller is actually an EI. Since `AuthenticateExternalInitiator` sets `SessionUserKey` too, `isUser` evaluates to `true` for EI-authenticated requests exactly as it does for genuine users. Consequently, an EI credential holder reaches `prc.App.RunJobV2(ctx, jobID, nil)` for whatever numeric `:ID` they supply, with zero verification that the calling `ei.Name`/identity is associated with that job at all — there is no per-job EI binding check anywhere in this path.

This matches the described attack: authenticate as EI A, then `POST /v2/jobs/{jobBelongingToEIB}/runs`, and the request is processed as if it came from an authorized user, because the code's only intended EI-exclusion check is defeated by the shared session key design.

### Impact Explanation
Unauthorized job run triggering: an attacker who only holds valid credentials for one external initiator can trigger pipeline runs on jobs they have no legitimate relationship to, bypassing the intended "requests are bound to exactly one authorized job/initiator" invariant. Depending on job type (e.g., jobs performing on-chain writes, VRF, keeper-style actions), an unauthorized run could cause unwanted on-chain transactions or resource exhaustion — this falls under "unauthorized job run" impact class.

### Likelihood Explanation
Requires only a single precondition: possession of any valid EI AccessKey/Secret pair (attacker-controlled, low-privilege credential). No admin/operator access needed. The bypass is deterministic and repeatable for any numeric job ID the attacker can guess or enumerate (sequential integer IDs are typical), making exploitation straightforward once one set of EI credentials is obtained.

### Recommendation
In `PipelineRunsController.Create`, explicitly check for the presence of `SessionExternalInitiatorKey` (via `auth.GetAuthenticatedExternalInitiator`) and reject the request if an EI is present, rather than relying on `GetAuthenticatedUser` returning false for EI sessions. Alternatively, stop `AuthenticateExternalInitiator` from writing to `SessionUserKey` at all (or use a distinct sentinel/role that `GetAuthenticatedUser`-based checks can safely differentiate), and require any legitimate EI-triggered run path to validate that the target job's `ExternalInitiatorSpec` matches the authenticated `ei.Name`/ID before invoking `RunJobV2`.

### Proof of Concept
1. Create two external initiators, EI-A and EI-B, each with distinct AccessKey/Secret, via `bridges.ORM.CreateExternalInitiator`.
2. Create Job J belonging conceptually to EI-B context (or simply any job with a valid numeric ID reachable via `RunJobV2`).
3. Build a `gin` test router wired with the real `POST /v2/jobs/:ID/runs` route and middleware stack from `core/web/router.go`.
4. Send `POST /v2/jobs/{J.ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI-A's credentials.
5. Assert (currently fails) that the response is `401/403` because EI-A is not authorized for job J.
6. Instead observe (bug) that `auth.GetAuthenticatedUser(c)` returns `ok=true` (dummy run-role user), `isUser` is true, `prc.App.RunJobV2` is invoked, and a `201`/pipeline-run resource is returned — demonstrating cross-initiator job triggering.

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
