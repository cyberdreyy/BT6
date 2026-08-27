Based on the code examined, the route `/v2/pipeline/runs` requires only session/token authentication with no role gate beyond that: `authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))` [1](#0-0)  is registered on the `authv2` group which only wraps `auth.Authenticate` (session or token), unlike other routes on the same group that are explicitly wrapped with `auth.RequiresEditRole`/`auth.RequiresRunRole`/`auth.RequiresAdminRole` [2](#0-1) . `PipelineRunsController.Index` performs no additional role or per-job ownership check — it simply calls `prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)` when no `:ID` param is present, returning runs across all jobs [3](#0-2) . The result is serialized via `presenters.NewPipelineRunResources`, which includes `Inputs` (the full resolved observation-source inputs) and each `PipelineTaskRunResource.Output` verbatim, with no redaction logic present in `core/web/presenters/pipeline_run.go` [4](#0-3) . The existing test confirms that bridge/data-source task outputs (e.g., `ds1`, `ds2` raw JSON bodies) are returned verbatim in the run listing response [5](#0-4) .

However, I was unable to fully verify within the available context whether bridge `RequestData`/`ResponseData` payloads that would contain **credentials** (e.g., an API key embedded in a header or body configured on the bridge/task spec) are actually captured into `TaskRun.Output` or pipeline `Inputs` as raw text, or whether the bridge task implementation in `core/services/pipeline/task.bridge.go` strips/omits secret-bearing fields (e.g. auth headers configured via `BridgeType.Confirmations`/external adapter config, which are typically added as HTTP headers by the bridge task and not stored in the visible `RequestData`/`ResponseData` JSON at all) before it's persisted to `pipeline_task_runs`. The proof-of-concept described in the question (embedding a token inside `RequestData` and confirming it round-trips into `TaskRun.Output`/`Inputs`) is required to establish that a credential — as opposed to ordinary observation-source response payload data (already demonstrated to leak) — is actually exposed. Since access to bridge authentication tokens/headers in Chainlink's `BridgeType` (`IncomingToken`/`OutgoingToken`) is stored separately from `RequestData` and is not embedded in the JSON that flows into `pipeline_runs`/`pipeline_task_runs`, I cannot confirm from the available index that this specific credential-exposure claim holds; only the broader claim that **all job run data (including full bridge task input/output payloads) is exposed to any authenticated view-role user with no per-job scoping** is confirmed.

Given the uncertainty about whether actual embedded credentials (vs. ordinary response data) are captured in `RequestData`/`ResponseData`, and per audit rules requiring concrete, verifiable credential disclosure rather than speculative payload content, I can only confirm the authorization-scope issue but not the specific "embedded API key in RequestData exposed" claim without deeper inspection of `task.bridge.go`'s persistence path, which I was not able to complete before running out of tool calls.

### Title
View-role user can enumerate all pipeline runs across all jobs via GET /v2/pipeline/runs, exposing full task run inputs/outputs with no per-job authorization scoping - ([File: core/web/router.go])

### Summary
The `GET /v2/pipeline/runs` route is registered under `authv2` with only session/token authentication and no role or per-job ownership check, allowing any authenticated user (including `view` role) to call `PipelineRunsController.Index` with no `:ID` and retrieve pipeline runs across all jobs in the node, including full task-run `Output` and pipeline `Inputs` fields verbatim.

### Finding Description
`v2Routes` registers `authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))` on the `authv2` group, which is wrapped only by `auth.Authenticate(...)` and not by any `auth.RequiresEditRole`/`RequiresRunRole`/`RequiresAdminRole` wrapper, unlike many sibling routes in the same file (e.g. `POST /bridge_types`, `POST /jobs`) [6](#0-5) . `PipelineRunsController.Index` branches on the `:ID` path param; when hitting `/v2/pipeline/runs` (no `:ID`), it calls `prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)`, which has no job filter and returns runs for every job in the node [7](#0-6) . The returned runs are converted with `presenters.NewPipelineRunResources`, which includes the full `Inputs` JSON and each task run's raw `Output` string with no field-level redaction [8](#0-7) . A test in the repo already demonstrates that data-source/bridge task response content (e.g., raw JSON bodies like `{"USD": 1}`) flows unredacted into this endpoint's response [9](#0-8) .

### Impact Explanation
A `view`-role user (the lowest privilege authenticated role) can read pipeline run data belonging to jobs they may not be intended to access, including task input/output content that could carry sensitive response data from external adapters. This matches an information-disclosure impact class if bridge/adapter payloads contain sensitive values (e.g., tokens embedded in adapter responses stored as task output). I could not fully confirm whether actual bridge authentication credentials specifically (`IncomingToken`/`OutgoingToken`) are captured in this same `RequestData`/`ResponseData` path versus being stored/used separately from the visible JSON payload, so the credential-specific claim in the question is not fully substantiated by the code reviewed.

### Likelihood Explanation
Only a valid session or API token with `view` role is required — the lowest privilege level in the system — and the endpoint requires no per-job authorization, making this trivially and repeatably reachable by any authenticated user regardless of intended job-level access scoping.

### Recommendation
Add an authorization/ownership check (or at minimum require `RequiresEditRole`/`RequiresRunRole`) to the global `/v2/pipeline/runs` listing route, and/or apply field-level redaction to `PipelineRunResource`/`PipelineTaskRunResource` (Inputs/Outputs) for sensitive adapter response content before serialization in `presenters.NewPipelineRunResource`.

### Proof of Concept
1. In a `core/web/pipeline_runs_controller_test.go`-style handler integration test, create two jobs (JobA, JobB) with bridge tasks, each producing distinguishable output/response content (e.g., unique marker strings).
2. Run both jobs via `app.RunJobV2`.
3. Create an HTTP client authenticated as a `view`-role user (`cltest` session/token helper with `sessions.User{Role: UserRoleView}`).
4. `GET /v2/pipeline/runs` and assert `http.StatusOK`, then assert the response body contains task output/markers from **both** JobA and JobB, proving cross-job data exposure to a view-role user with no ownership scoping.
5. (Additional verification needed) To confirm the specific credential-exposure claim, configure a bridge task whose adapter response body embeds a token substring, and grep the endpoint response for that substring.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L399-401)
```go
		authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs/:runID", prc.Show)
```

**File:** core/web/pipeline_runs_controller.go (L29-53)
```go
func (prc *PipelineRunsController) Index(c *gin.Context, size, page, offset int) {
	id := c.Param("ID")

	// Temporary: if no size is passed in, use a large page size. Remove once frontend can handle pagination
	if c.Query("size") == "" {
		size = 1000
	}

	var pipelineRuns []pipeline.Run
	var count int
	var err error

	ctx := c.Request.Context()
	if id == "" {
		pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)
	} else {
		jobSpec := job.Job{}
		err = jobSpec.SetID(c.Param("ID"))
		if err != nil {
			jsonAPIError(c, http.StatusUnprocessableEntity, err)
			return
		}

		pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, &jobSpec.ID, offset, size)
	}
```

**File:** core/web/presenters/pipeline_run.go (L35-97)
```go
func NewPipelineRunResource(pr pipeline.Run, lggr logger.Logger) PipelineRunResource {
	lggr = lggr.Named("PipelineRunResource")
	trs := make([]PipelineTaskRunResource, 0, len(pr.PipelineTaskRuns))
	for i := range pr.PipelineTaskRuns {
		trs = append(trs, NewPipelineTaskRunResource(pr.PipelineTaskRuns[i]))
	}

	outputs, err := pr.StringOutputs()
	if err != nil {
		lggr.Errorw(err.Error(), "out", pr.Outputs)
	}

	fatalErrors := pr.StringFatalErrors()

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
}

// Corresponds with models.d.ts PipelineTaskRun
type PipelineTaskRunResource struct {
	Type       pipeline.TaskType `json:"type"`
	CreatedAt  time.Time         `json:"createdAt"`
	FinishedAt null.Time         `json:"finishedAt"`
	Output     *string           `json:"output"`
	Error      *string           `json:"error"`
	DotID      string            `json:"dotId"`
}

// GetName implements the api2go EntityNamer interface
func (r PipelineTaskRunResource) GetName() string {
	return "taskRun"
}

func NewPipelineTaskRunResource(tr pipeline.TaskRun) PipelineTaskRunResource {
	var output *string
	if tr.Output.Valid {
		outputBytes, _ := tr.Output.MarshalJSON()
		outputStr := string(outputBytes)
		output = &outputStr
	}
	var errString *string
	if tr.Error.Valid {
		errString = &tr.Error.String
	}
	return PipelineTaskRunResource{
		Type:       tr.Type,
		CreatedAt:  tr.CreatedAt,
		FinishedAt: tr.FinishedAt,
		Output:     output,
		Error:      errString,
		DotID:      tr.GetDotID(),
	}
}
```

**File:** core/web/pipeline_runs_controller_test.go (L88-101)
```go
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
```
