### Title
Unauthorized cross-job exposure of pipeline run Inputs/Outputs (bridge/EI response data) to view-role users via GET /v2/pipeline/runs - ([File: core/web/pipeline_runs_controller.go])

### Summary
The `/v2/pipeline/runs` route is registered with only `auth.Authenticate` (session or token) and no role gate, so any authenticated user — including one whose role is `UserRoleView` — can call `PipelineRunsController.Index` with `id==""` and retrieve up to 1000 pipeline runs across *all* jobs on the node, including full `Inputs`/`Outputs` payloads that can contain bridge/EI response bodies embedded by tasks.

### Finding Description
`v2Routes` registers `authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))` [1](#0-0)  inside the `authv2` group, which is protected only by `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` [2](#0-1) . Unlike other sensitive routes in the same file (e.g. `/replay_from_block`, `/find_lca`, `/keys/*`, `/jobs`), this route is not wrapped with `auth.RequiresRunRole`, `auth.RequiresEditRole`, or `auth.RequiresAdminRole` [3](#0-2) .

In `PipelineRunsController.Index`, when the request path has no `:ID` param (i.e. `/pipeline/runs` rather than `/jobs/:ID/runs`), `id` is empty and the handler fetches all pipeline runs across every job with no job-scoping filter: `pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)` [4](#0-3) . If the `size` query parameter is omitted, the handler defaults `size` to 1000 [5](#0-4) , maximizing the amount of data returned in one call.

The results are converted via `presenters.NewPipelineRunResources`, which does not redact/scrub `Inputs`/`Outputs` — it directly serializes `pr.Inputs` and the pipeline's `Outputs` (via `StringOutputs()`) verbatim into the JSON response [6](#0-5) . `pipeline.Run.Inputs`/`Outputs` are `jsonserializable.JSONSerializable` fields that store arbitrary task input/output values, including raw bridge/EI HTTP response bodies captured by tasks (e.g. HTTP/Bridge task adapters) [7](#0-6) .

`auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` are the only mechanisms in this codebase that differentiate `UserRoleView` from higher roles [8](#0-7) ; since `Index` for `/pipeline/runs` has none of these wrappers, a `UserRoleView` user's request passes `auth.Authenticate` and reaches the handler with full access, returning inputs/outputs for jobs the requester may have no legitimate operational need to see, node-wide. The existing test suite confirms unredacted output for the job-scoped route and demonstrates the same unredacted content appears for the global `/pipeline/runs` route: `TestPipelineRunsController_Index_GlobalHappyPath` asserts the raw `inputs`/`outputs` JSON is present in the response from `GET /v2/pipeline/runs` [9](#0-8) .

### Impact Explanation
This is a cross-tenant/cross-job information disclosure: a low-privilege (`view`-role) authenticated node user can enumerate pipeline run history for every job on the node — not just jobs they were granted access to (Chainlink’s role model is node-wide, not per-job, but the intent of the role hierarchy is to gate write/run/admin capability, and this endpoint exposes bulk sensitive run payloads without any of the role checks applied to comparable endpoints). If bridge/EI adapters embed sensitive data (API keys reflected in error bodies, EI response payloads, PII, proprietary data feed content) inside `Outputs`/`Inputs`, that data becomes retrievable in bulk by any credential holder with only view-level access, which is a stronger capability than intended for that role and matches an "information disclosure of secret-bearing data via API" impact class.

### Likelihood Explanation
Exploitation requires only a valid user session or API token with the lowest role (`view`) — no admin, no per-job entitlement, no special network position. The request is a single unauthenticated-w.r.t.-role `GET /v2/pipeline/runs` call; it is trivially repeatable and enumerable via pagination (`page`, `size` params), and defaults to returning up to 1000 runs per call. This makes it fully reproducible and low-effort for any authenticated low-privilege user.

### Recommendation
Wrap the `/v2/pipeline/runs` (and ideally `/jobs/:ID/runs`, `/jobs/:ID/runs/:runID`) routes with an explicit role check consistent with the sensitivity of the data being returned — e.g. `auth.RequiresRunRole` or a dedicated role, or restrict the `id==""` (global) branch to require `RequiresAdminRole`/`RequiresEditRole` since it discloses data across jobs. Alternatively, redact/limit `Inputs`/`Outputs` content in `presenters.NewPipelineRunResources` for the global listing, or remove the unauthenticated "no id" global-listing behavior from the public API surface. Add a role-based test verifying that `view`-role users cannot access `/v2/pipeline/runs` or receive redacted content.

### Proof of Concept
Go handler-level integration test (extending `core/web/pipeline_runs_controller_test.go`):
1. Reuse `setupPipelineRunsControllerTests` helper but seed a `pipeline.Run` whose `Outputs`/`Inputs` contain a marker string simulating a bridge response secret, e.g. `{"bridgeResponse":"SECRET-API-KEY-12345"}`.
2. Create an authenticated HTTP client for a user with `Role: clsessions.UserRoleView` (using `app.NewHTTPClient` with a view-role user, similar to existing role-based tests like those for `/keys/*` or `/replay_from_block`).
3. Call `client.Get("/v2/pipeline/runs")`.
4. Assert `http.StatusOK` is returned (not `401`/`403`), and assert the response body contains the seeded marker string `SECRET-API-KEY-12345`, proving a view-role user obtained sensitive Inputs/Outputs data for jobs outside any explicit per-job grant.
5. Contrast with a comparable role-gated endpoint (e.g. `/v2/replay_from_block/:number`) called by the same view-role user, which correctly returns `401 Unauthorized` via `auth.RequiresRunRole`, to demonstrate the inconsistency/missing gate on `/v2/pipeline/runs`.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L298-302)
```go
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
		lcaC := LCAController{app}
		authv2.GET("/find_lca", auth.RequiresRunRole(lcaC.FindLCA))
		lpSkipC := LPSkipController{app}
		authv2.POST("/lp_skip_to_block", auth.RequiresRunRole(lpSkipC.LPSkipToBlock))
```

**File:** core/web/router.go (L399-399)
```go
		authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))
```

**File:** core/web/pipeline_runs_controller.go (L32-35)
```go
	// Temporary: if no size is passed in, use a large page size. Remove once frontend can handle pagination
	if c.Query("size") == "" {
		size = 1000
	}
```

**File:** core/web/pipeline_runs_controller.go (L42-43)
```go
	if id == "" {
		pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)
```

**File:** core/web/presenters/pipeline_run.go (L49-60)
```go
	return PipelineRunResource{
		JAID:         NewJAIDInt64(pr.ID),
		Outputs:      outputs,
		Errors:       fatalErrors,
		AllErrors:    pr.StringAllErrors(),
		FatalErrors:  fatalErrors,
		Inputs:       pr.Inputs,
		TaskRuns:     trs,
		CreatedAt:    pr.CreatedAt,
		FinishedAt:   pr.FinishedAt,
		PipelineSpec: NewPipelineSpec(&pr.PipelineSpec),
	}
```

**File:** core/services/pipeline/models.go (L49-67)
```go
type Run struct {
	ID             int64                             `json:"-"`
	JobID          int32                             `json:"-"`
	PipelineSpecID int32                             `json:"-"`
	PruningKey     int32                             `json:"-"` // This currently refers to the upstream job ID
	PipelineSpec   Spec                              `json:"pipelineSpec"`
	Meta           jsonserializable.JSONSerializable `json:"meta"`
	// The errors are only ever strings
	// DB example: [null, null, "my error"]
	AllErrors   RunErrors                         `json:"all_errors"`
	FatalErrors RunErrors                         `json:"fatal_errors"`
	Inputs      jsonserializable.JSONSerializable `json:"inputs"`
	// Its expected that Output.Val is of type []interface{}.
	// DB example: [1234, {"a": 10}, null]
	Outputs          jsonserializable.JSONSerializable `json:"outputs"`
	CreatedAt        time.Time                         `json:"createdAt"`
	FinishedAt       null.Time                         `json:"finishedAt"`
	PipelineTaskRuns []TaskRun                         `json:"taskRuns"`
	State            RunStatus                         `json:"state"`
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

**File:** core/web/pipeline_runs_controller_test.go (L74-102)
```go
func TestPipelineRunsController_Index_GlobalHappyPath(t *testing.T) {
	t.Parallel()

	client, jobID, runIDs := setupPipelineRunsControllerTests(t)

	url := url.URL{Path: "/v2/pipeline/runs"}
	query := url.Query()
	query.Set("evmChainID", cltest.FixtureChainID.String())
	url.RawQuery = query.Encode()

	response, cleanup := client.Get(url.String())
	defer cleanup()
	cltest.AssertServerResponse(t, response, http.StatusOK)

	var parsedResponse []presenters.PipelineRunResource
	responseBytes := cltest.ParseResponseBody(t, response)
	assert.Contains(t, string(responseBytes), `"outputs":["3"],"errors":[null],"allErrors":["uh oh"],"fatalErrors":[null],"inputs":{"answer":"3","ds1":"{\"USD\": 1}","ds1_multiply":"3","ds1_parse":1,"ds2":"{\"USD\": 1}","ds2_multiply":"3","ds2_parse":1,"ds3":{},"jobRun":{"meta":null}`)

	err := web.ParseJSONAPIResponse(responseBytes, &parsedResponse)
	require.NoError(t, err)

	require.Len(t, parsedResponse, 2)
	// Job Run ID is returned in descending order by run ID (most recent run first)
	assert.Equal(t, parsedResponse[1].ID, strconv.Itoa(int(runIDs[0])))
	assert.NotNil(t, parsedResponse[1].CreatedAt)
	assert.NotNil(t, parsedResponse[1].FinishedAt)
	assert.Equal(t, jobID, parsedResponse[1].PipelineSpec.JobID)
	require.Len(t, parsedResponse[1].TaskRuns, 8)
}
```
