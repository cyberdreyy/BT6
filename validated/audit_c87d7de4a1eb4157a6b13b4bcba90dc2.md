### Title
Cross-job pipeline run disclosure via unscoped `FindRun` lookup in `Show` handler - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Show` handles `GET /v2/jobs/:ID/runs/:runID` but never validates that the run identified by `runID` actually belongs to the job identified by the `ID` path parameter. It parses only `runID`, calls `prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)`, and returns whatever run is found, ignoring the job scoping entirely.

### Finding Description
In `Show` (core/web/pipeline_runs_controller.go:67-84), the job ID path segment `c.Param("ID")` is never read or compared to the run's owning job. The handler only parses `c.Param("runID")` into a `pipeline.Run{}` and passes the resulting numeric run ID directly to `PipelineORM().FindRun(ctx, pipelineRun.ID)` [1](#0-0) . Contrast this with `Index` (same file, lines 29-62), which explicitly parses `job.Job{}` from `c.Param("ID")` and passes `&jobSpec.ID` into `PipelineRuns(ctx, &jobSpec.ID, ...)` to scope results to that job [2](#0-1) . `Show` has no equivalent check — any authenticated caller who knows or guesses a valid `runID` can retrieve it by hitting any job's `/runs/:runID` route, since the `ID` segment is never used to filter or authorize the lookup.

### Impact Explanation
Pipeline run resources returned via `presenters.NewPipelineRunResource` include full task run inputs/outputs, which may contain sensitive data (API keys, bridge secrets, off-chain data query parameters, or oracle responses) tied to a different job than the one referenced in the URL. This maps to Chainlink's "unauthorized disclosure of sensitive information" bounty impact class — cross-job/cross-user response confusion leaking run data outside its intended job-scoped path.

### Likelihood Explanation
Any credential with API access sufficient to hit `/v2/jobs/:ID/runs/:runID` (e.g., a view-role or run-role authenticated user) can exploit this if run IDs are sequential/enumerable, since run IDs are simple auto-increment integers assigned by the ORM rather than job-scoped UUIDs. No special privilege beyond generic authenticated API access is required, and the flaw is deterministic and repeatable on every request.

### Recommendation
In `Show`, parse the job ID from `c.Param("ID")` as done in `Index`/`Create`, and verify the fetched `pipelineRun.PipelineSpec.JobID` (or equivalent job association) matches the requested job before returning the resource; return 404 if it doesn't match. Alternatively, change `FindRun` (or add a new ORM method) to accept both job ID and run ID and scope the SQL query accordingly.

### Proof of Concept
1. Create two jobs, JobA and JobB, each triggering a pipeline run (e.g., via webhook or `RunJobV2`), capturing `runA.ID` and `runB.ID`.
2. As an authenticated view-role user, issue `GET /v2/jobs/{JobA.ID}/runs/{runB.ID}`.
3. Assert the handler does not return 404/403, but instead returns HTTP 200 with `runB`'s pipeline run resource (inputs/outputs matching JobB, not JobA) — proving cross-job disclosure.
4. Add a handler-level integration test in `core/web/pipeline_runs_controller_test.go` asserting `resp.StatusCode == http.StatusNotFound` (or equivalent) when `runID` does not belong to the given job `ID`, which should fail against current code and pass after the fix.

### Citations

**File:** core/web/pipeline_runs_controller.go (L42-53)
```go
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

**File:** core/web/pipeline_runs_controller.go (L67-84)
```go
func (prc *PipelineRunsController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	pipelineRun := pipeline.Run{}
	err := pipelineRun.SetID(c.Param("runID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	pipelineRun, err = prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	res := presenters.NewPipelineRunResource(pipelineRun, prc.App.GetLogger())
	jsonAPIResponse(c, res, "pipelineRun")
}
```
