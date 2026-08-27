Confirmed: `RunJobV2` (called by `PipelineRunsController.Create`) returns an error on secure/production builds via `build.IsProd()` [1](#0-0)  — this is a defense-in-depth check that would block this exploit in production binaries, but does not exist to bind an EI to a specific job; it's a blanket dev-only guard. On non-prod/dev builds, or if that guard were bypassed/removed, the underlying authorization bug still holds.

### Title
External-initiator credentials are not bound to any specific job, allowing cross-job run triggering via POST /v2/jobs/:ID/runs - ([File: core/web/pipeline_runs_controller.go])

### Summary
`auth.AuthenticateExternalInitiator` authenticates any valid EI access-key/secret pair and unconditionally injects a synthetic `&clsessions.User{Role: UserRoleRun}` into the Gin context, with no association to the job ID being requested. `PipelineRunsController.Create` then treats this synthetic user identically to a real session/token user (`auth.GetAuthenticatedUser` cannot distinguish the two), and calls `RunJobV2(jobID)` for whatever integer `:ID` was supplied in the URL, without ever checking `auth.GetAuthenticatedExternalInitiator(c)` against the target job.

### Finding Description
The route is wired as: [2](#0-1) 

`auth.AuthenticateExternalInitiator` looks up the EI purely by `AccessKey`/`Secret` header values, verifies the HMAC/secret match, and then sets both the EI object and a generic run-role `User` object on the context — with no job-scoping information at all: [3](#0-2) 

`auth.RequiresRunRole` only checks `user.Role != View`; it has no concept of which job the caller is entitled to run: [4](#0-3) 

Inside `PipelineRunsController.Create`, the code's own comment claims "only users are allowed to run jobs using int IDs - EIs not allowed," and gates on `isUser := auth.GetAuthenticatedUser(c)`: [5](#0-4) 

However `GetAuthenticatedUser` simply reads whatever was stored under `SessionUserKey`: [6](#0-5) 

Since `AuthenticateExternalInitiator` stores a `*clsessions.User{Role: UserRoleRun}` under that exact same key, `isUser` evaluates to `true` for EI-authenticated requests too — the intended EI exclusion is dead code. The handler then parses `:ID` as an int32 and calls `prc.App.RunJobV2(ctx, jobID, nil)` for that ID with no check that the authenticated EI (obtainable via `auth.GetAuthenticatedExternalInitiator(c)`) has any relationship to that job: [7](#0-6) 

There is no data model or lookup anywhere in this path that ties `bridges.ExternalInitiator.ID`/`Name` to a specific `job.Job.ID` for authorization purposes (the legacy webhook `externalInitiators` spec binding existed only for the now-removed Webhook job type, and `Create` explicitly rejects UUID-style webhook IDs before this integer-ID code path is reached).

### Impact Explanation
Any holder of valid credentials for one external initiator can trigger a pipeline run on **any** job in the node by supplying its integer job ID, not just jobs the initiator was registered against. Chainlink bounty impact class: unauthorized job run / request impersonation via authorization/role-binding bypass, scoped to triggering runs on jobs the attacker's initiator identity was never authorized for. Depending on job pipeline tasks (e.g., ETH transactions, external HTTP calls with side effects, VRF fulfillment logic), this could cause unwanted on-chain transactions or resource exhaustion/DoS from repeated triggering of unrelated jobs.

### Likelihood Explanation
Preconditions are minimal: possession of one valid EI `AccessKey`/`Secret` pair (the lowest external-initiator credential tier, obtained legitimately for a single job/integration) and knowledge/guessing of a numeric job ID for a different job. No admin/operator access is required. The bug is fully deterministic and repeatable — every request with valid EI headers against `/v2/jobs/<any-int-id>/runs` follows this same code path. The only mitigating factor found is the `build.IsProd()` guard in `RunJobV2`, which blocks *all* manual runs (regardless of caller) on production builds; on non-prod builds, or for any code path that reintroduces manual-run support in production, the authorization gap is fully exploitable.

### Recommendation
1. Fix `PipelineRunsController.Create` to correctly detect EI-authenticated requests via `auth.GetAuthenticatedExternalInitiator(c)` (not `GetAuthenticatedUser`), and reject or properly scope EI-triggered runs.
2. If EI-triggered runs by integer job ID are intended to be supported at all, persist and check a mapping from `bridges.ExternalInitiator` to the specific job(s) it is authorized to trigger, and enforce that binding in `Create` before calling `RunJobV2`.
3. Consider not injecting a `clsessions.User` object at all for EI auth, using a distinct context type/key so `GetAuthenticatedUser`/`RequiresRunRole` cannot be satisfied by EI credentials silently.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go` patterns and `cltest.CreateExternalInitiatorViaWeb`):
1. Start `cltest.NewApplicationWithConfig` with `ExternalInitiatorsEnabled = true`.
2. Create two jobs via `AddJobV2`/web API — Job A and Job B — with distinct int32 IDs (non-webhook, non-UUID types, e.g. simple fetch/OCR specs as used in `runner_integration_test.go`).
3. Create External Initiator "EI-A" via `cltest.CreateExternalInitiatorViaWeb`, obtaining `AccessKey`/`Secret`.
4. Using `cltest.UnauthenticatedPost` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-AccessSecret` set to EI-A's credentials, POST to `/v2/jobs/<JobB.ID>/runs` (Job B, which EI-A has no relation to).
5. Assert: response should be `403 Forbidden`/`401 Unauthorized` (expected fix) — but currently (on a non-prod build) it succeeds and returns a `pipelineRun` resource for Job B, and `pipeline_runs` table gains a row for Job B triggered by EI-A's identity, proving cross-job run triggering with no binding check.
6. On a build with `build.IsProd()==true`, additionally assert that `RunJobV2` currently blocks this uniformly for both real users and EIs via `"manual job runs not supported on secure builds"`, confirming that any environment where manual/EI-triggered runs are re-enabled in production must implement job-scoped EI authorization instead of relying solely on this build flag.

### Citations

**File:** core/services/chainlink/application.go (L1126-1141)
```go
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
	var runID int64

	// Some jobs are special in that they do not have a task graph.
	isBootstrap := jb.Type == job.OffchainReporting && jb.OCROracleSpec != nil && jb.OCROracleSpec.IsBootstrapPeer
```

**File:** core/web/router.go (L449-456)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
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

**File:** core/web/auth/auth.go (L177-186)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
```

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
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

**File:** core/web/pipeline_runs_controller.go (L89-128)
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
}
```
