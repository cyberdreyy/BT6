### Title
EI-authenticated requests bypass job binding and can trigger runs for any int-ID job via `RequiresRunRole` + `PipelineRunsController.Create` - ([File: core/web/auth/auth.go] / [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` unconditionally sets `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` for any valid EI credential, with no check tying the EI to a specific job/webhook spec. [1](#0-0)  Because the webhook job type has been removed from the codebase, `PipelineRunsController.Create` no longer performs any UUID→EI-binding lookup at all; it only special-cases UUID-shaped IDs to reject them, and otherwise treats any authenticated principal (user OR external initiator, since both populate `SessionUserKey`) as authorized to call `RunJobV2` for an arbitrary integer job ID. [2](#0-1) 

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered with `auth.Authenticate(..., auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` followed by `auth.RequiresRunRole(prc.Create)`. [3](#0-2) 

In `AuthenticateExternalInitiator`, once the EI access key/secret pair validates against `FindExternalInitiator`/`bridges.AuthenticateExternalInitiator`, the code sets both `SessionExternalInitiatorKey` and `SessionUserKey` (role `Run`) with no reference to which job the EI was intended for: [4](#0-3) 

`RequiresRunRole` only checks `user.Role != View`, so any EI (role Run) passes. [5](#0-4) 

Inside `PipelineRunsController.Create`, the `idStr` path param is checked: if it parses as a UUID, the request is rejected outright because "Webhook runs used external job UUIDs; that job type has been removed" — i.e. the legacy `external_initiator_webhook_specs` join-table binding check that used to gate EI-to-job association no longer exists in this code path at all. [6](#0-5)  If the ID is a plain int32, the code calls `isUser, _ := auth.GetAuthenticatedUser(c)` and, if true, invokes `prc.App.RunJobV2(ctx, jobID, nil)` for that job unconditionally: [7](#0-6) 

The comment states "only users are allowed to run jobs using int IDs - EIs not allowed", but the actual check (`auth.GetAuthenticatedUser`) reads `SessionUserKey`, which `AuthenticateExternalInitiator` also populates with a synthetic `User{Role: UserRoleRun}`. [8](#0-7)  There is no code path that distinguishes an EI-authenticated request from a real user/token/session-authenticated request at this point, and no lookup of `external_initiator_webhook_specs` (or any equivalent binding) is performed for int-ID jobs. Thus a credential minted for EI "A" can trigger `RunJobV2` for **any** integer job ID in the system — not merely a job unrelated to EI A, but any job of any type (OCR, VRF, Cron, etc.) that exists on the node.

### Impact Explanation
This is unauthorized triggering of pipeline runs for jobs the External Initiator credential was never associated with, matching Chainlink's "unauthorized job run" bounty impact class. Depending on job configuration (e.g. jobs with on-chain transmission side effects, VRF fulfillment triggers, or jobs using bridges/external tasks), an attacker holding any single EI credential could force pipeline executions on arbitrary jobs, potentially causing unintended on-chain transactions, resource exhaustion, or interference with unrelated job owners' pipelines.

### Likelihood Explanation
Preconditions are minimal: possession of any valid EI AccessKey/Secret pair (which could be created via `POST /external_initiators` by any user with edit role, or leaked/reused from a legitimate low-trust integration) is sufficient. No job-specific binding, admin access, or additional privilege is required. The attack is fully reproducible via a single authenticated HTTP POST to `/v2/jobs/<int-job-id>/runs` using the EI headers.

### Recommendation
In `PipelineRunsController.Create`, explicitly check whether the request was authenticated via `auth.GetAuthenticatedExternalInitiator(c)` and, if so, either reject the request entirely (since the only intended EI use case — UUID-keyed webhook jobs — has been removed) or re-introduce an explicit binding check confirming the EI is associated with the target job ID before calling `RunJobV2`. Do not rely on `SessionUserKey` alone to gate int-ID job runs, since `AuthenticateExternalInitiator` also populates that key.

### Proof of Concept
Handler-level integration test plan (extends `pipeline_runs_controller_test.go`):
1. Start an app, create an External Initiator `eiA` via `cltest.CreateExternalInitiatorViaWeb`.
2. Create a non-webhook job (e.g., a cron or bridge-based pipeline job) `jobB` with an integer ID, with no reference to `eiA` anywhere.
3. Send `POST /v2/jobs/<jobB.ID>/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `eiA`'s credentials (using `cltest.UnauthenticatedPost`, matching the pattern in `TestRunner_WebhookJobRemoved`).
4. Assert current (vulnerable) behavior: response is `200 OK` and a `pipeline_runs` row is created for `jobB`, despite `eiA` never being associated with `jobB`.
5. Expected secure behavior: response should be `401`/`403` because no `external_initiator`-to-`jobB` binding exists; add an assertion that the rejection is specifically due to lack of authorization tied to the job, not merely an unrelated validation error (e.g. bad job ID format).

### Citations

**File:** core/web/auth/auth.go (L119-150)
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

**File:** core/web/pipeline_runs_controller.go (L100-124)
```go

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
```

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```
