### Title
Cross-job pipeline run disclosure via unvalidated `:runID` in `GET /v2/jobs/:ID/runs/:runID` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Show` looks up a pipeline run purely by `:runID` via `FindRun`, without ever checking that the returned run's `PipelineSpec.JobID`/job association matches the `:ID` job segment of the URL. Any authenticated user (including a view-role user) who knows or can enumerate a numeric `runID` belonging to a different job can retrieve that run's full resource, including task inputs/outputs, by putting an arbitrary job `:ID` in the path.

### Finding Description
The `Show` handler is: [1](#0-0) 

It parses `c.Param("runID")` into `pipelineRun.SetID(...)`, then calls `prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)`. The `FindRun` ORM method signature only takes the run `id`, not a job ID: [2](#0-1) 

`c.Param("ID")` (the job ID from the URL) is never read or used anywhere in `Show` — contrast with `Index`, which explicitly parses `c.Param("ID")` and passes the job ID into `PipelineRuns(ctx, &jobSpec.ID, ...)` to scope results to that job: [3](#0-2) 

Since `Show` never verifies that the fetched run belongs to the job identified by `:ID`, a request to `GET /v2/jobs/999/runs/<runID belonging to job 42>` will succeed and return job 42's run data (task inputs/outputs) as long as the `runID` exists in the database and the requester passes the route's role-based auth check (view role is sufficient for a GET). This is a broken object-level authorization (IDOR)-style issue: the path segment `:ID` is decorative and not enforced as an authorization boundary.

### Impact Explanation
An authenticated user with only view privileges can read another job's pipeline run data — including task inputs and outputs, which may contain sensitive data (e.g., API responses, computed values, potentially secrets embedded in task configs/results) — by supplying any job ID together with a `runID` from a different job. This is a cross-tenant/cross-job information disclosure within a single node's job set, matching the "unauthorized data disclosure / cross-user response confusion" bounty impact class.

### Likelihood Explanation
Exploitability requires only a valid session with view role (the least-privileged authenticated role) and knowledge of, or ability to enumerate, a numeric run ID. Run IDs are typically sequential integers, and view-role users can already discover their own jobs' run IDs via `Index`/`Create`, making enumeration of nearby IDs from unrelated jobs straightforward. The request is a single unauthenticated-in-role GET call, fully repeatable, with no rate limiting evident in this handler.

### Recommendation
In `Show`, after resolving `c.Param("ID")` into a job spec/ID (as `Index` already does), verify that `pipelineRun.PipelineSpec.JobID` (or equivalent association) matches the parsed job ID before returning the resource; return 404/`http.StatusNotFound` if there is a mismatch. Alternatively, change `FindRun` (or add a new ORM method) to accept both `runID` and `jobID` and constrain the SQL query by both, failing closed when they don't correspond to the same job.

### Proof of Concept
Handler-level integration test plan (Go, using existing patterns in `core/web/pipeline_runs_controller_test.go`):
1. Create Job A and Job B via the job ORM/pipeline ORM test helpers.
2. Create a pipeline run for Job A (`runA`) and a separate run for Job B (`runB`), each with distinct task input/output payloads.
3. Authenticate as a view-role user/session.
4. Send `GET /v2/jobs/<JobA.ID>/runs/<runB.ID>`.
5. Assert: expected behavior is `404 Not Found` (or equivalent rejection) because `runB` does not belong to Job A.
6. Actual (current) behavior: assert the response is `200 OK` and the returned `pipelineRun` JSON resource's data (task inputs/outputs) matches `runB`, proving job A's URL discloses job B's run — confirming the vulnerability.

### Citations

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

**File:** core/services/pipeline/orm.go (L41-41)
```go
	FindRun(ctx context.Context, id int64) (Run, error)
```
