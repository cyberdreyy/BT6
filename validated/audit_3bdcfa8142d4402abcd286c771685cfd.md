Confirmed: `ExternalInitiatorsEnabled()` is only checked in one place across the entire codebase — `ExternalInitiatorsController.Create` [1](#0-0) . The `AuthenticateExternalInitiator` middleware, which is wired into the router to guard `POST /v2/jobs/:ID/runs` (webhook job-run triggering) and `GET /v2/ping`, never checks this flag [2](#0-1) [3](#0-2) .

### Title
External Initiator authentication and job-run triggering remain active after `ExternalInitiatorsEnabled` is disabled - (File: core/web/auth/auth.go)

### Summary
The `JobPipeline.ExternalInitiatorsEnabled` configuration toggle is intended to enable/disable the External Initiator (EI) feature. It is enforced only at credential-creation time in `ExternalInitiatorsController.Create` [1](#0-0) , but it is never checked at the point where EI credentials are actually used to authenticate and trigger job runs.

### Finding Description
`AuthenticateExternalInitiator` looks up the EI record by access key/secret headers and, on success, sets the caller's role to `UserRoleRun`, without any check of `App.GetConfig().JobPipeline().ExternalInitiatorsEnabled()`: [3](#0-2) 

This middleware is mounted on the `userOrEI` route group in `v2Routes`, guarding `GET /v2/ping` and, critically, `POST /v2/jobs/:ID/runs` (the endpoint used to trigger a webhook job run): [2](#0-1) 

A repository-wide search shows `ExternalInitiatorsEnabled()` is referenced in exactly one production code path — the `Create` handler — and nowhere in the authentication/authorization path or the run-triggering handler: [4](#0-3) [5](#0-4) 

This is the same bug class as the Mochi `withdrawLock()` finding: a toggle meant to gate a sensitive capability is checked at one entry point (creation) but not enforced at the actual point of use (authentication/execution), so pre-existing EI credentials remain fully functional after the feature is "disabled."

### Impact Explanation
If an operator disables the External Initiator feature (e.g., in response to a security incident, credential leak, or simply because they believed it shuts off webhook-triggered runs), any external initiator credentials created prior to the toggle change (or otherwise known to an attacker, e.g. via leakage) can still authenticate via `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and call `POST /v2/jobs/:ID/runs` to trigger job runs — bypassing the operator's intended access restriction. Since job runs can move funds or trigger on-chain transactions, this can result in unauthorized job execution.

### Likelihood Explanation
Likelihood is dependent on operational usage: an operator must have created EI credentials at some point before disabling the flag (or such credentials must otherwise be compromised). Given `ExternalInitiatorsEnabled` defaults to `false`, an operator toggling it on to create initiators and later off again to "revoke" the capability would reasonably expect existing credentials to stop working — this is a plausible real-world misconfiguration expectation.

### Recommendation
Add an explicit check of `App.GetConfig().JobPipeline().ExternalInitiatorsEnabled()` inside `AuthenticateExternalInitiator` (or in the `userOrEI` route group middleware chain) so that when the feature is disabled, EI-based authentication fails regardless of whether valid credentials exist in the database.

### Proof of Concept
1. Start a node with `JobPipeline.ExternalInitiatorsEnabled = true`.
2. Create an external initiator via `POST /v2/external_initiators` and record the returned `AccessKey`/`Secret` [6](#0-5) .
3. Set `JobPipeline.ExternalInitiatorsEnabled = false` and restart/reload the node config.
4. Issue `POST /v2/jobs/:ID/runs` with headers `X-Chainlink-EA-AccessKey` and `X-Chainlink-EA-Secret` set to the credentials from step 2, targeting the `userOrEI` route group defined at [7](#0-6) .
5. Observe the request succeeds and triggers the job run, because `AuthenticateExternalInitiator` never checks the disabled flag.

### Citations

**File:** core/web/external_initiators_controller.go (L62-99)
```go
func (eic *ExternalInitiatorsController) Create(c *gin.Context) {
	ctx := c.Request.Context()
	eir := &bridges.ExternalInitiatorRequest{}
	if !eic.App.GetConfig().JobPipeline().ExternalInitiatorsEnabled() {
		err := errors.New("The External Initiator feature is disabled by configuration")
		jsonAPIError(c, http.StatusMethodNotAllowed, err)
		return
	}

	eia := auth.NewToken()
	if err := c.ShouldBindJSON(eir); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	ei, err := bridges.NewExternalInitiator(eia, eir)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	if err := ValidateExternalInitiator(ctx, eir, eic.App.BridgeORM()); err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}
	if err := eic.App.BridgeORM().CreateExternalInitiator(ctx, ei); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	eic.App.GetAuditLogger().Audit(audit.ExternalInitiatorCreated, map[string]any{
		"externalInitiatorID":   ei.ID,
		"externalInitiatorName": ei.Name,
		"externalInitiatorURL":  ei.URL,
	})

	resp := presenters.NewExternalInitiatorAuthentication(*ei, *eia)
	jsonAPIResponseWithStatus(c, resp, "external initiator authentication", http.StatusCreated)
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

**File:** core/config/job_pipeline_config.go (L1-19)
```go
package config

import (
	"time"

	commonconfig "github.com/smartcontractkit/chainlink-common/pkg/config"
)

type JobPipeline interface {
	DefaultHTTPLimit() int64
	DefaultHTTPTimeout() commonconfig.Duration
	MaxRunDuration() time.Duration
	MaxSuccessfulRuns() uint64
	ReaperInterval() time.Duration
	ReaperThreshold() time.Duration
	ResultWriteQueueDepth() uint64
	ExternalInitiatorsEnabled() bool
	VerboseLogging() bool
}
```

**File:** core/services/chainlink/config_job_pipeline.go (L45-47)
```go
func (j *jobPipelineConfig) ExternalInitiatorsEnabled() bool {
	return *j.c.ExternalInitiatorsEnabled
}
```
