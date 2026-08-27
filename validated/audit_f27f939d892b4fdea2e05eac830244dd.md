### Title
External Initiator credentials for one job can trigger pipeline runs of any other job via `/v2/jobs/:ID/runs`, bypassing the job-to-initiator binding — ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` looks up the EI purely by `AccessKey` and, on success, sets `SessionUserKey` to a synthetic `User{Role: UserRoleRun}` in addition to `SessionExternalInitiatorKey`. `PipelineRunsController.Create` then only checks `auth.GetAuthenticatedUser(c)` (which is now true for both real users and EIs) and calls `App.RunJobV2(ctx, jobID, nil)` using the raw `:ID` path parameter, without ever consulting `GetAuthenticatedExternalInitiator` to verify the caller's EI is actually bound to that job's initiator spec.

### Finding Description
- `AuthenticateExternalInitiator` (core/web/auth/auth.go:119-151) authenticates solely against `store.FindExternalInitiator(ctx, eia)`, which is keyed only by `AccessKey` (`SELECT * FROM external_initiators WHERE access_key = $1`, e.g. core/bridges/orm.go:263-267). There is no job ID in this lookup.
- After successful password verification, the middleware sets both `c.Set(SessionExternalInitiatorKey, ei)` and `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` (auth.go:143-148). This second line means any handler using `GetAuthenticatedUser` cannot distinguish an EI-authenticated request from a genuine session/token-authenticated user.
- The route `POST /v2/jobs/:ID/runs` is registered in `userOrEI` group (core/web/router.go:450-456) which accepts `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession`, wrapped only by `auth.RequiresRunRole` (which just checks `user.Role != View`, auth.go:200-217) — no job/EI cross-check.
- `PipelineRunsController.Create` (core/web/pipeline_runs_controller.go:89-128) reads `idStr := c.Param("ID")`, checks `_, isUser := auth.GetAuthenticatedUser(c)` (line 109), and if `isUser` is true (which is unconditionally true for an authenticated EI, per the point above), parses `idStr` as an `int32` job ID and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly — with **no reference at all** to `auth.GetAuthenticatedExternalInitiator(c)` or any check that this EI's name/access key matches the job's `externalInitiators` config.
- `RunJobV2` (core/services/chainlink/application.go:1126-1189) loads the job purely by numeric ID (`app.jobORM.FindJob(ctx, jobID)`) and executes its pipeline — it performs no EI-ownership check either.

Consequently, an EI credential holder valid for job A can submit `POST /v2/jobs/<jobB-int-id>/runs` with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` for EI "foo", and the handler will trigger job B's pipeline run, even though "foo" was never configured as an initiator for job B.

**Important mitigating factor found during review:** `RunJobV2` begins with `if build.IsProd() { return 0, errors.New("manual job runs not supported on secure builds") }` and is commented "Only used for local testing, not supported by the UI." This means on official/secure (production) Chainlink builds, this code path always errors out before reaching `FindJob`/pipeline execution, which would prevent exploitation on production release binaries. The vulnerability is real and reachable at the code level for non-prod builds (dev/test builds), but its real-world impact on production-flagged binaries is blocked by this separate guard. I was unable to fully confirm within this session how `build.IsProd()` is set for standard released binaries (its definition was not found in the indexed files), so the practical reach on official production nodes remains partially uncertain.

### Impact Explanation
If `build.IsProd()` is false (e.g., non-release/dev/test builds, or any deployment not compiled with the production build tag), this is an authorization bypass allowing request impersonation: an unprivileged holder of any single external initiator's credentials can trigger arbitrary job runs belonging to other jobs/users on the same node, which can result in unauthorized pipeline execution (side effects such as bridge calls, on-chain transactions for jobs with ETHTx tasks, VRF fulfillment attempts, etc., depending on job type) — mapping to Chainlink's "Unauthorized job run" / authorization bypass impact class.

### Likelihood Explanation
- Preconditions: attacker needs valid EI credentials for any one registered external initiator (obtainable if they operate any legitimate external initiator integration), and the target node must not be running with `build.IsProd()==true`.
- No admin/host access required; only knowledge of another job's public/guessable integer job ID (job IDs are sequential integers, easily enumerable via `/v2/jobs` if edit role, or simply guessed since they're small sequential ints).
- Exploit is a single crafted HTTP POST, fully repeatable.
- Overall likelihood is contingent on the `build.IsProd()` gate; this significantly limits real-world exploitability against production-released Chainlink node binaries, but the flawed authorization logic itself is a code-level defect independent of that gate.

### Recommendation
1. In `PipelineRunsController.Create`, distinguish EI-authenticated requests from real user sessions explicitly (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` first) and, when the caller is an EI, verify that the target job's initiator configuration (webhook/external initiator spec) actually references this EI's name/ID before invoking `RunJobV2`.
2. Avoid overloading `SessionUserKey` for EI auth with a role-only synthetic user that is indistinguishable from a genuine user session; instead, have `RequiresRunRole`/handlers query `GetAuthenticatedExternalInitiator` explicitly wherever EI identity matters.
3. Remove reliance on `build.IsProd()` alone as a security boundary for `RunJobV2`; the authorization check should be correct in all build modes.

### Proof of Concept
Handler-level Go test plan (module `core/web`, similar to `pipeline_runs_controller_test.go`):
1. Start an app with two webhook-capable jobs, job A and job B, each with distinct `externalInitiators` blocks. Register EI "foo" via `cltest.CreateExternalInitiatorViaWeb` and bind it only to job A's TOML `externalInitiators` config.
2. Insert job B similarly bound to a different EI "bar" (or no EI at all).
3. Build request: `POST /v2/jobs/<jobB.ID>/runs` (numeric ID) with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI "foo"'s credentials.
4. Assert expected secure behavior: `403 Forbidden` (or `404`) and `cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", 0)` for job B.
5. Run test with a non-prod build tag; observe current behavior returns `201 Created` with a new pipeline run tied to job B's pipeline spec — demonstrating the missing EI-to-job binding check (test will fail against current code, confirming the bug), except where `build.IsProd()` short-circuits `RunJobV2`, which should also be explicitly tested/toggled. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
}
```

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

**File:** core/services/chainlink/application.go (L1125-1137)
```go
// Only used for local testing, not supported by the UI.
func (app *ChainlinkApplication) RunJobV2(
	ctx context.Context,
	jobID int32,
	meta map[string]any,
) (int64, error) {
	if build.IsProd() {
		return 0, errors.New("manual job runs not supported on secure builds")
	}
	jb, err := app.jobORM.FindJob(ctx, jobID)
	if err != nil {
		return 0, errors.Wrapf(err, "job ID %v", jobID)
	}
```
