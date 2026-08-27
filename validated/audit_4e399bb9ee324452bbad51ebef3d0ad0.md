### Title
Authorization check in `PipelineRunsController.Create` relies on generic `isUser`/role flag instead of External-Initiator identity, allowing an EI credential to trigger arbitrary jobs by integer ID - (File: core/web/pipeline_runs_controller.go)

### Summary
`auth.AuthenticateExternalInitiator` correctly authenticates the caller as a *specific* `ExternalInitiator` record (looked up by `access_key`, verified against that record's own `Salt`/`HashedSecret`), but it then discards this specific identity and replaces it with a generic `clsessions.User{Role: clsessions.UserRoleRun}` in the gin context. Downstream, `PipelineRunsController.Create` decides whether to run an arbitrary job purely from `auth.GetAuthenticatedUser(c)` returning `ok==true` (i.e. "is *something* logged in with a User object"), not from whether the caller is actually the External Initiator entitled to that particular job.

### Finding Description
`AuthenticateExternalInitiator` looks up the EI strictly by `access_key` and verifies the secret against that specific EI's stored salt/hash: [1](#0-0) 

Critically, on success it sets both the EI object *and* a synthetic generic user:
```go
c.Set(SessionExternalInitiatorKey, ei)
c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
``` [2](#0-1) 

`PipelineRunsController.Create` then makes its authorization decision using only the generic user check `isUser`:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
``` [3](#0-2) 

The comment states the intent is "EIs not allowed" to use int IDs, but the code never inspects `auth.GetAuthenticatedExternalInitiator(c)` to actually exclude EI-authenticated requests or to bind the request to the specific EI's own job/bridge. Because `AuthenticateExternalInitiator` populates the same `SessionUserKey` used by real session/token-authenticated users, `isUser` is `true` for an EI-authenticated request exactly as for a genuine logged-in user. `RunJobV2` itself performs no re-check that the caller's EI identity matches the target job's associated external initiator/bridge - it just runs the job by numeric ID: [4](#0-3) 

The only barrier currently preventing full exploitation is that UUID-style job IDs (historically used to scope webhook/EI job runs) are unconditionally rejected up-front because the webhook job type has been removed: [5](#0-4) 
This is confirmed by the integration test showing an EI-authenticated POST to a job's UUID always returns `422 Unprocessable Entity` for the "job type removed" reason: [6](#0-5) 

However, this rejection happens **before** the `isUser`/int32 branch is even reached, and it only fires because the ID is parsed as a UUID. It does not fix the underlying authorization gap: if an attacker holding valid EI A credentials sends a request to `/v2/jobs/<int32-job-id>/runs` where `<int32-job-id>` is any job's numeric ID (obtainable e.g. via `GET /jobs`, which is not role-restricted beyond authentication), `isUser` still evaluates to `true` from the EI-issued generic role, and `RunJobV2` will be invoked for that job with no verification that EI A is the initiator associated with it. Nowhere in this path is `GetAuthenticatedExternalInitiator(c)` cross-checked against the job's own external-initiator/bridge configuration.

### Impact Explanation
An attacker who legitimately possesses one ExternalInitiator's `AccessKey`/`Secret` pair (scoped, by design, to a specific bridge/EI) can trigger pipeline runs for **any** job addressable by an int32 ID on the node, not just jobs tied to their EI. This is an authorization/role-bypass allowing unauthorized job-run triggering across bridges/jobs, matching the "unauthorized job run" bounty impact class.

### Likelihood Explanation
Preconditions: attacker only needs one valid (even narrowly-scoped) EI credential pair - the same minimal credential level assumed in the question. Job IDs are small sequential integers and are further discoverable via the authenticated `GET /jobs` listing endpoint, which is not restricted beyond basic authentication. The exploit requires no elevated privileges and is fully repeatable via a single crafted HTTP POST.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject requests authenticated via `AuthenticateExternalInitiator` (i.e., where `auth.GetAuthenticatedExternalInitiator(c)` succeeds) from the int32/`RunJobV2` branch, rather than relying on the coincidental `isUser` check that both real users and EIs satisfy. If EI-triggered runs by numeric ID are ever reintroduced, the handler must look up the job's associated `ExternalInitiator`/bridge and verify it matches the authenticated `ei.ID`/`ei.Name` before calling `RunJobV2`.

### Proof of Concept
1. Create two External Initiators/bridges, EI-A and EI-B, and two jobs, JobA (int32 ID) associated conceptually with bridge/EI-A workflow, JobB with EI-B.
2. Authenticate as EI-A using `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers.
3. POST to `/v2/jobs/<JobB numeric ID>/runs` using EI-A's credentials.
4. Assert the response is `200 OK` with a created pipeline run for JobB (via `prc.App.PipelineORM().FindRun`), demonstrating that EI-A's credentials triggered a run for a job it has no configured association with, rather than expecting `401 Unauthorized` or `403 Forbidden`.
5. Additionally assert `auth.GetAuthenticatedExternalInitiator(c)` inside the handler returns EI-A while the job executed belongs to a different bridge/initiator, confirming the identity is never checked against the target job.

### Citations

**File:** core/web/auth/auth.go (L119-151)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
```

**File:** core/web/pipeline_runs_controller.go (L86-128)
```go
// Create triggers a pipeline run for a job.
// Example:
// "POST <application>/jobs/:ID/runs"
func (prc *PipelineRunsController) Create(c *gin.Context) {
	ctx := c.Request.Context()
	respondWithPipelineRun := func(jobRunID int64) {
		pipelineRun, err := prc.App.PipelineORM().FindRun(ctx, jobRunID)
		if err != nil {
			jsonAPIError(c, http.StatusInternalServerError, err)
			return
		}
		res := presenters.NewPipelineRunResource(pipelineRun, prc.App.GetLogger())
		jsonAPIResponse(c, res, "pipelineRun")
	}

	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}

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

**File:** core/services/job/runner_integration_test.go (L836-847)
```go
	job, _ := cltest.MustInsertWebhookSpec(t, app.GetDB(), jobUUID)

	runBody := cltest.MustJSONMarshal(t, eiRequest)
	headers := map[string]string{
		static.ExternalInitiatorAccessKeyHeader: eia.AccessKey,
		static.ExternalInitiatorSecretHeader:    eia.Secret,
	}
	url := app.Server.URL + "/v2/jobs/" + jobUUID.String() + "/runs"
	resp, cleanup := cltest.UnauthenticatedPost(t, url, bytes.NewBufferString(runBody), headers)
	defer cleanup()
	cltest.AssertServerResponse(t, resp, http.StatusUnprocessableEntity)
	cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", 0)
```
