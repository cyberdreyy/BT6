### Title
External Initiator credentials bypass the "EIs not allowed" check and can trigger runs on arbitrary numeric job IDs - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
`PipelineRunsController.Create` gates numeric-job-ID runs with `_, isUser := auth.GetAuthenticatedUser(c)` and a comment stating "only users are allowed to run jobs using int IDs - EIs not allowed" [1](#0-0) . However, `AuthenticateExternalInitiator` — the auth method used for external-initiator-authenticated routes — sets the very same `SessionUserKey` that `GetAuthenticatedUser` reads, populating it with a synthetic user object (`&clsessions.User{Role: clsessions.UserRoleRun}`) after successfully validating the EI's access key/secret [2](#0-1) . `GetAuthenticatedUser` only checks for presence of `SessionUserKey` in the gin context, with no distinction between a real logged-in user and this synthetic EI-derived user, and no check against `GetAuthenticatedExternalInitiator` [3](#0-2) . Consequently, `isUser` evaluates to `true` for any request authenticated solely via valid EI credentials, and the handler proceeds to call `prc.App.RunJobV2(ctx, jobID, nil)` for whatever numeric job ID is present in the URL path, with zero verification that the job is associated with, or authorized for, that specific external initiator [4](#0-3) . This directly contradicts the code's own documented intent and the required invariant that an EI request must bind to exactly one authorized job.

### Impact Explanation
Any holder of a valid external-initiator access key/secret pair (a low-privilege, narrowly-scoped credential intended only to trigger its own webhook job) can invoke `RunJobV2` against any numeric job ID in the node, including jobs it has no legitimate relationship to. This is an authorization/role-bypass vulnerability enabling unauthorized job runs — potentially triggering fund-moving or state-changing pipelines belonging to unrelated jobs, matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Exploitation requires only a single valid EI credential (access key + secret) already provisioned by the node operator, and knowledge/guessing of a target job's integer ID (IDs are small sequential integers and are frequently discoverable via job list responses or predictable sequencing). No admin/host access is needed, and the attack is fully repeatable via a simple HTTP POST, making likelihood high once one EI is compromised or legitimately possessed.

### Recommendation
Change the check in `PipelineRunsController.Create` to explicitly exclude external-initiator-derived sessions, e.g. reject the request if `auth.GetAuthenticatedExternalInitiator(c)` returns `ok == true`, rather than relying solely on `GetAuthenticatedUser` presence. Alternatively, stop `AuthenticateExternalInitiator` from populating `SessionUserKey` with a synthetic user and instead thread EI identity through a distinct mechanism that downstream handlers can positively check for and reject in "EIs not allowed" code paths.

### Proof of Concept
1. Set up a node with an `ExternalInitiator` record and a webhook job unrelated to that EI, plus a second unrelated job B with an integer ID.
2. Send `POST /v2/jobs/<jobB-ID>/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` (per `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader`) set to the EI's valid credentials, and no session cookie or API token.
3. Assert the middleware chain authenticates via `AuthenticateExternalInitiator` (sets `SessionExternalInitiatorKey` and `SessionUserKey`) [2](#0-1) .
4. Assert `PipelineRunsController.Create` computes `isUser == true` and calls `prc.App.RunJobV2(ctx, jobB.ID, nil)`, returning HTTP 200 with a created pipeline run resource, despite job B having no association with the EI used to authenticate.
5. Compare against expected behavior: the request should be rejected with `422 bad job ID` (or similar), consistent with the code's stated intent that "EIs not allowed" for int-ID job runs.

### Citations

**File:** core/web/pipeline_runs_controller.go (L109-111)
```go
	_, isUser := auth.GetAuthenticatedUser(c)
	// only users are allowed to run jobs using int IDs - EIs not allowed
	if isUser {
```

**File:** core/web/pipeline_runs_controller.go (L112-124)
```go
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

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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
