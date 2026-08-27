Confirmed: the `GET /v2/jobs/:ID` route has no role wrapper, and the presenter serializes `GatewayConfig`/`RelayConfig` verbatim into the response.

### Title
UserRoleView session can read GatewaySpec.gatewayConfig / OCR2 relayConfig secrets via unrestricted `GET /v2/jobs/:ID` - (File: core/web/router.go)

### Summary
`GET /v2/jobs/:ID` is registered as `authv2.GET("/jobs/:ID", jc.Show)` with no `auth.RequiresEditRole`/`RequiresRunRole`/`RequiresAdminRole` wrapper, unlike `Create`/`Update`/`Delete` on the same resource. Any authenticated session, including the lowest-privileged `UserRoleView`, can call `Show`, which returns the full `GatewaySpec.GatewayConfig` map and `OffChainReporting2Spec.RelayConfig`/`BootstrapSpec.RelayConfig` maps unredacted.

### Finding Description
In `v2Routes` (`core/web/router.go`), the job CRUD routes are: [1](#0-0) 

`Create`, `Update`, `Delete` require edit role, but `Index` and `Show` have no role gate at all — only the base `authv2` group's `auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)` middleware, which merely validates the session/token and attaches the `User` to context without checking `Role`.

`JobsController.Show` (`core/web/jobs_controller.go`) looks the job up by ID/external ID and directly serializes it with `presenters.NewJobResource(jobSpec)` — no role-based field filtering: [2](#0-1) 

The presenter passes through the raw config maps for Gateway and OCR2/Bootstrap specs: [3](#0-2) [4](#0-3) 

The only role checks in the codebase are the `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` wrapper functions in `core/web/auth/auth.go`: [5](#0-4) 

None of these wrap `jc.Show` or `jc.Index`, so a `UserRoleView` session (the lowest role, intended to be read-only for non-sensitive data) passes straight through to the presenter with no redaction of `gatewayConfig`/`relayConfig` fields, both of which are free-form `map[string]any` populated directly from the job spec TOML that operators may embed API keys, webhook secrets, or auth tokens into (e.g., gateway node/DON auth keys, EA credentials referenced by relay config).

### Impact Explanation
This matches Chainlink's "secret/credential disclosure via authorization bypass" impact class: an unprivileged (relative to edit/admin) authenticated user gains read access to job configuration data — including potentially secret-bearing `gatewayConfig`/`relayConfig` blobs — that the route design (as evidenced by the edit-role gating on mutation endpoints) treats as sensitive enough to protect on write, but not on read. Impact is scoped to whatever secrets operators choose to embed in these config maps (this is not database credential theft or RCE, but unauthorized disclosure of job configuration to a lower-privilege authenticated principal than intended).

### Likelihood Explanation
Requires only a valid `UserRoleView` session (the minimum authenticated role) and knowledge/guessability of a job ID or external job ID — both trivially obtainable since `GET /v2/jobs` (`Index`) is similarly unrestricted and lists all jobs. No additional preconditions, timing, or race conditions are needed; the request is a single unauthenticated-w.r.t.-role GET call, fully repeatable.

### Recommendation
Decide the intended read-access policy for job specs containing secrets:
- If `Show`/`Index` should remain viewable by `UserRoleView` (as is standard for other read-only GET routes like `/bridge_types/:BridgeName`, `/keys/*`), redact/strip secret-bearing fields (`gatewayConfig`, `relayConfig`) from `presenters.GatewaySpec`/`OffChainReporting2Spec`/`BootstrapSpec` before serialization, similar to how other presenters mask secrets.
- Alternatively, wrap `authv2.GET("/jobs/:ID", jc.Show)` (and `Index`) with `auth.RequiresEditRole` if job configs are meant to be edit/admin-gated for reading, consistent with the mutation endpoints.

### Proof of Concept
Go handler-level integration test plan (using existing `cltest` helpers, e.g. patterns in `core/web/jobs_controller_test.go`):
1. Set up a test app via `cltest.NewApplicationWithConfig`/`setupJobsControllerTests` helpers.
2. Create a Gateway job via `job.ValidatedGatewaySpec`/`jc.App.AddJobV2` (or via authenticated edit-role client) whose TOML embeds a distinctive secret string in `GatewayConfig`, e.g. `"nodes": [{"apiSecret": "SUPER_SECRET_TOKEN_1234"}]`.
3. Create a session with `cltest.User{Role: sessions.UserRoleView}` via `cltest.MustGenerateSessionID`/`cltest.MockSessionRequestBuilder` (mirroring existing view-role tests, e.g. `TestJobController_Configuration` patterns already using `UserRoleView`/`UserRoleRun` for negative testing on Create/Update/Delete).
4. Issue `GET /v2/jobs/<jobID>` using the view-role session cookie.
5. Assert:
   - HTTP status is `200 OK` (not `401`/`403`), proving no role gate blocks the request.
   - Response body (unmarshaled `JobResource`/raw JSON) contains `"SUPER_SECRET_TOKEN_1234"` in `gatewaySpec.gatewayConfig`.
6. As a control, repeat the same request against `POST /v2/jobs`, `PUT /v2/jobs/:ID`, `DELETE /v2/jobs/:ID` with the same view-role session and assert `401 Unauthorized`, confirming the inconsistency between read and write role enforcement on the same resource.

### Citations

**File:** core/web/router.go (L391-396)
```go
		jc := JobsController{app}
		authv2.GET("/jobs", paginatedRequest(jc.Index))
		authv2.GET("/jobs/:ID", jc.Show)
		authv2.POST("/jobs", auth.RequiresEditRole(jc.Create))
		authv2.PUT("/jobs/:ID", auth.RequiresEditRole(jc.Update))
		authv2.DELETE("/jobs/:ID", auth.RequiresEditRole(jc.Delete))
```

**File:** core/web/jobs_controller.go (L67-91)
```go
func (jc *JobsController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	var err error
	jobSpec := job.Job{}
	if externalJobID, pErr := uuid.Parse(c.Param("ID")); pErr == nil {
		// Find a job by external job ID
		jobSpec, err = jc.App.JobORM().FindJobByExternalJobID(ctx, externalJobID)
	} else if pErr = jobSpec.SetID(c.Param("ID")); pErr == nil {
		// Find a job by job ID
		jobSpec, err = jc.App.JobORM().FindJob(ctx, jobSpec.ID)
	} else {
		jsonAPIError(c, http.StatusUnprocessableEntity, pErr)
		return
	}
	if err != nil {
		if errors.Is(errors.Cause(err), sql.ErrNoRows) {
			jsonAPIError(c, http.StatusNotFound, errors.New("job not found"))
		} else {
			jsonAPIError(c, http.StatusInternalServerError, err)
		}
		return
	}

	jsonAPIResponse(c, presenters.NewJobResource(jobSpec), "jobs")
}
```

**File:** core/web/presenters/job.go (L170-206)
```go
// OffChainReporting2Spec defines the spec details of a OffChainReporting2 Job
type OffChainReporting2Spec struct {
	ContractID                        string           `json:"contractID"`
	Relay                             string           `json:"relay"` // RelayID.Network
	RelayConfig                       map[string]any   `json:"relayConfig"`
	P2PV2Bootstrappers                pq.StringArray   `json:"p2pv2Bootstrappers"`
	OCRKeyBundleID                    null.String      `json:"ocrKeyBundleID"`
	TransmitterID                     null.String      `json:"transmitterID"`
	ObservationTimeout                sqlutil.Interval `json:"observationTimeout"`
	BlockchainTimeout                 sqlutil.Interval `json:"blockchainTimeout"`
	ContractConfigTrackerPollInterval sqlutil.Interval `json:"contractConfigTrackerPollInterval"`
	ContractConfigConfirmations       uint16           `json:"contractConfigConfirmations"`
	OnchainSigningStrategy            map[string]any   `json:"onchainSigningStrategy"`
	CreatedAt                         time.Time        `json:"createdAt"`
	UpdatedAt                         time.Time        `json:"updatedAt"`
	CollectTelemetry                  bool             `json:"collectTelemetry"`
}

// NewOffChainReporting2Spec initializes a new OffChainReportingSpec from a
// job.OCR2OracleSpec
func NewOffChainReporting2Spec(spec *job.OCR2OracleSpec) *OffChainReporting2Spec {
	return &OffChainReporting2Spec{
		ContractID:                        spec.ContractID,
		Relay:                             spec.Relay,
		RelayConfig:                       spec.RelayConfig,
		P2PV2Bootstrappers:                spec.P2PV2Bootstrappers,
		OCRKeyBundleID:                    spec.OCRKeyBundleID,
		TransmitterID:                     spec.TransmitterID,
		BlockchainTimeout:                 spec.BlockchainTimeout,
		ContractConfigTrackerPollInterval: spec.ContractConfigTrackerPollInterval,
		ContractConfigConfirmations:       spec.ContractConfigConfirmations,
		OnchainSigningStrategy:            spec.OnchainSigningStrategy,
		CreatedAt:                         spec.CreatedAt,
		UpdatedAt:                         spec.UpdatedAt,
		CollectTelemetry:                  spec.CaptureEATelemetry,
	}
}
```

**File:** core/web/presenters/job.go (L406-418)
```go
type GatewaySpec struct {
	GatewayConfig map[string]any `json:"gatewayConfig"`
	CreatedAt     time.Time      `json:"createdAt"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}

func NewGatewaySpec(spec *job.GatewaySpec) *GatewaySpec {
	return &GatewaySpec{
		GatewayConfig: spec.GatewayConfig,
		CreatedAt:     spec.CreatedAt,
		UpdatedAt:     spec.UpdatedAt,
	}
}
```

**File:** core/web/auth/auth.go (L200-255)
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

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}

// RequiresAdminRole extracts the user object from the context, and asserts the user's role is 'admin'
func RequiresAdminRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role != clsessions.UserRoleAdmin {
			c.Abort()
			addForbiddenErrorHeaders(c, "admin", string(user.Role), user.Email)
			jsonAPIError(c, http.StatusForbidden, errors.New("Forbidden"))
			return
		}
		handler(c)
	}
}
```
