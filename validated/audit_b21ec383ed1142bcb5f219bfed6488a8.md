### Title
External Initiator credentials for any EI can trigger a run of any job by numeric ID via `PipelineRunsController.Create`, with no binding check between the authenticated EI and the target job/bridge - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go], [File: core/web/router.go])

### Summary
`auth.AuthenticateExternalInitiator` authenticates solely by looking up the access key/secret in the `external_initiators` table via `FindExternalInitiator`, without any awareness of the specific job or route being invoked, and on success grants the generic `UserRoleRun` role. The route `POST /v2/jobs/:ID/runs` accepts this role from any external initiator and, in `PipelineRunsController.Create`, calls `RunJobV2` for the numeric job ID with no check that the job is associated with the authenticated EI or any bridge scoping at all.

### Finding Description
The middleware chain for `POST /v2/jobs/:ID/runs` is: [1](#0-0) 

`AuthenticateExternalInitiator` builds an `auth.Token` purely from request headers (`ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader`), looks it up via `store.FindExternalInitiator(ctx, eia)` (which does `SELECT * FROM external_initiators WHERE access_key = $1`), verifies the secret hash, and then sets a generic session user with `Role: clsessions.UserRoleRun` — it never records or checks which EI's job/bridge context the request is being made against: [2](#0-1) [3](#0-2) 

Inside `PipelineRunsController.Create`, the handler only checks `isUser` via `auth.GetAuthenticatedUser(c)`, which reads the `SessionUserKey` set by `AuthenticateExternalInitiator` — an EI-authenticated request therefore satisfies `isUser == true`. For any numeric job ID it directly calls `prc.App.RunJobV2(ctx, jobID, nil)` with zero validation that the job belongs to the specific external initiator that authenticated, or any external initiator at all: [4](#0-3) 

Because webhook jobs (the type that historically carried the `externalInitiators = [...]` binding metadata) have been removed (`job.ErrJobTypeRemoved`), and the route accepts any numeric `:ID`, there is no code path anywhere in `Create` that cross-references the authenticated EI's identity (`ei.Name`/`ei.ID` from `bridges.ExternalInitiator`) against the target job's configuration. Any EI credential (EI-A's) satisfies the `RequiresRunRole` check and can invoke `RunJobV2` for a job that was never associated with EI-A (e.g., one intended for EI-B, or any OCR/VRF/Flux-monitor/cron job with no EI association at all).

### Impact Explanation
This allows an unprivileged holder of any single external-initiator's access key/secret to trigger execution of an arbitrary job on the node by supplying its numeric job ID — not limited to jobs tied to that EI. This is an unauthorized job run (matching the "unauthorized job run" bounty impact class), and depending on the job's pipeline (e.g., a job that moves funds, calls a bridge with side effects, or writes on-chain) this can cause unintended fund movement or resource exhaustion/DoS via repeated forced pipeline execution, using credentials scoped to a completely different, unrelated integration.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs valid access-key/secret credentials for any single registered external initiator (a low-privilege credential type, explicitly intended only to trigger jobs for its own registered webhook integration) and knowledge/guessability of a target job's numeric ID (job IDs are visible via `GET /v2/jobs` to admins, and may be enumerable/small sequential integers). No admin, session, or API-token-with-elevated-role access is required. The exploit is fully repeatable and requires no timing or race conditions.

### Recommendation
Bind the authenticated external initiator's identity to the specific job at authorization time: in `PipelineRunsController.Create`, when the authenticated principal is an EI (retrieve it via `c.Get(SessionExternalInitiatorKey)` rather than only checking the generic `isUser`/role), look up the job spec and verify it was created with `externalInitiators` referencing that specific EI (by name/ID) before calling `RunJobV2`. Reject the request with 401/403 if no such binding exists. Since webhook/EI job types have been removed, consider removing the `AuthenticateExternalInitiator` method from this route entirely if external initiators are no longer meant to trigger arbitrary job runs.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go` style and reusing helpers from `core/services/job/runner_integration_test.go`):
1. Start an app via `cltest.NewApplicationWithConfig`.
2. Create two external initiators, EI-A and EI-B, via `cltest.CreateExternalInitiatorViaWeb`, capturing `eip.AccessKey`/`eip.Secret` for each.
3. Create Job-B (e.g., a simple non-webhook job, such as the OCR job used in `setupPipelineRunsControllerTests`) that has no relationship to EI-A whatsoever (only conceptually "belongs" to EI-B's integration/bridge in this scenario, or simply is unrelated to any EI).
4. Send `POST /v2/jobs/<Job-B numeric ID>/runs` using EI-A's `ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader`.
5. Assert (current/expected-vulnerable behavior): response is `200 OK` with a `PipelineRunResource`, and `RunJobV2` was actually invoked (verify a new row in `pipeline_runs` for Job-B).
6. Assert (expected/fixed behavior after remediation): response should be `401 Unauthorized` or `403 Forbidden` because EI-A has no binding to Job-B, and no new `pipeline_runs` row should be created — use `cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", <original count>)`.

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

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
}
```

**File:** core/web/pipeline_runs_controller.go (L89-127)
```go
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
```
