<br>

No vulnerability found for this question.

**Analysis basis:**

`strconv.ParseInt(idStr, 10, 32)` in `PipelineRunsController.Create` uses bitSize `32`, so any string that would numerically exceed the `int32` range (e.g., `99999999999`) fails to parse with `strconv.ErrRange` and falls through to the `"bad job ID"` 422 response — it never reaches `RunJobV2`. [1](#0-0) 

For values that do parse (including negative numbers like `-1`), `RunJobV2` immediately calls `app.jobORM.FindJob(ctx, jobID)` before doing anything else, which is a real existence check against the database. [2](#0-1) 

Since job IDs are positive DB serial values, a negative or non-existent ID simply causes `FindJob` to return `sql.ErrNoRows` (wrapped), which the controller surfaces as a `500` error with no pipeline run created — there is no collision or unintended execution path, and no attacker-controlled data is returned to another user. [3](#0-2) 

The GraphQL equivalent (`Resolver.RunJob`) follows the same pattern via `stringutils.ToInt32` and `RunJobV2`/`FindJob`, with identical existence-check protection. [4](#0-3) 

Also note the referenced file path in the question (`core/sessions/ldapauth/client.go`) is unrelated to `PipelineRunsController.Create`, which actually lives in `core/web/pipeline_runs_controller.go`.

### Citations

**File:** core/web/pipeline_runs_controller.go (L112-124)
```go
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

**File:** core/services/chainlink/application.go (L1126-1137)
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
```

**File:** core/web/resolver/mutation.go (L1213-1228)
```go
func (r *Resolver) RunJob(ctx context.Context, args struct {
	ID graphql.ID
}) (*RunJobPayloadResolver, error) {
	if err := authenticateUserCanRun(ctx); err != nil {
		return nil, err
	}

	jobID, err := stringutils.ToInt32(string(args.ID))
	if err != nil {
		return nil, err
	}

	jobRunID, err := r.App.RunJobV2(ctx, jobID, nil)
	if err != nil {
		return nil, err
	}
```
