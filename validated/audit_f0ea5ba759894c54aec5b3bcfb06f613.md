### Title
Job-type-removal guard for Webhook jobs is format-based, not type-based, allowing bypass via integer databaseID - ([File: core/web/pipeline_runs_controller.go])

### Finding Description
`PipelineRunsController.Create` rejects a run request only when the `:ID` path parameter parses as a UUID, and only then returns the "job type webhook has been removed" error: [1](#0-0) 

If the same parameter instead parses as an `int32`, the handler unconditionally forwards it to `prc.App.RunJobV2` without ever checking the job's actual `Type`: [2](#0-1) 

`RunJobV2` itself performs no type-based rejection for `job.Webhook` — it only special-cases `job.VRF` and OCR bootstrap peers, and otherwise blindly executes the job's pipeline spec if `RequiresPipelineSpec()` is true: [3](#0-2) 

Meanwhile, `job.ORM.CreateJob` blocks *creation* of new Webhook jobs, but the CHANGELOG and code comments confirm the DB rows/tables for pre-existing Webhook jobs were intentionally left intact ("Database tables ... have not been removed, but jobs will no longer start"): [4](#0-3) 

Test helpers such as `cltest.MustInsertWebhookSpec` and `TestRunner_WebhookJobRemoved` demonstrate that Webhook-type job rows with a valid integer `job.ID` (`databaseID`) and a separate `ExternalJobID` (UUID) can and do exist in the DB independent of the removal: [5](#0-4) 

The existing test `TestPipelineRunsController_RunExistingWebhookJobRejected` only exercises the UUID path (`/v2/jobs/<uuid>/runs`) and never verifies the integer-ID path for a Webhook job, so the gap in `Create` is untested: [6](#0-5) 

Because the removal guard keys off the *format* of the path parameter (UUID vs. int) rather than the *actual persisted job type*, a POST to `/v2/jobs/<databaseID>/runs` for a legacy Webhook job entirely skips the "job type has been removed" check and reaches `RunJobV2`, which will execute that job's pipeline (bridge calls, HTTP tasks, etc.) exactly as before removal.

### Impact Explanation
This breaks the intended security/compatibility guarantee that "Webhook jobs will no longer start" post-removal. A caller who is authenticated with only run-role (not admin) can still trigger execution of a job type the node operator/Chainlink explicitly disabled, causing that job's pipeline (which may include bridge/API calls, external initiator side effects, or downstream fund-moving tasks configured in the pipeline) to execute unexpectedly. This is an authorization-exactness violation — the guard's intent (block all Webhook execution) is not enforced by all code paths, and this maps to Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
Requires: (1) a pre-existing legacy Webhook job in the database (plausible post-upgrade since rows are explicitly retained), (2) attacker knowledge of that job's integer `databaseID` (sequential/low-entropy, unlike its UUID), and (3) an authenticated session with run-role (the code comment explicitly states int-ID runs are reserved for authenticated users, not EIs). This is feasible for any internal/API-token holder with run permissions who can enumerate small integer job IDs, and is repeatable via a simple `POST /v2/jobs/<int>/runs`.

### Recommendation
In `PipelineRunsController.Create`, after resolving the job (or before calling `RunJobV2`), look up the job's `Type` and reject with the same `job.ErrJobTypeRemoved` error if `jb.Type == job.Webhook` (and the other removed types `DirectRequest`/`FluxMonitor`), regardless of whether the path parameter was a UUID or an integer. Equivalently, add the same type check inside `RunJobV2` itself in `core/services/chainlink/application.go` so all callers (REST, GraphQL `RunJob`) are protected uniformly.

### Proof of Concept
Handler-level integration test (extending `pipeline_runs_controller_test.go`):
1. Start app, use `cltest.MustInsertWebhookSpec` to insert a Webhook-type job directly (bypassing `CreateJob`'s block), capturing its integer `job.ID`.
2. `POST /v2/jobs/<job.ID>/runs` (using the integer databaseID, not the UUID) as an authenticated user.
3. Assert the response is `422 Unprocessable Entity` containing "job type webhook has been removed" (same as the UUID path) — currently this assertion **fails**, since the request instead proceeds to `RunJobV2` and either succeeds or returns a different (500/pipeline-execution) error, and `pipeline_runs` count increases contrary to `cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", 0)` used in the analogous UUID test.

### Citations

**File:** core/web/pipeline_runs_controller.go (L101-107)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}
```

**File:** core/web/pipeline_runs_controller.go (L109-124)
```go
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

**File:** core/services/chainlink/application.go (L1126-1189)
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
	if jb.Type.RequiresPipelineSpec() || !isBootstrap {
		var vars map[string]any
		var saveTasks bool
		if jb.Type == job.VRF {
			saveTasks = true
			// Create a dummy log to trigger a run
			testLog := types.Log{
				Data: bytes.Join([][]byte{
					jb.VRFSpec.PublicKey.MustHash().Bytes(),  // key hash
					common.BigToHash(big.NewInt(42)).Bytes(), // seed
					evmutils.NewHash().Bytes(),               // sender
					evmutils.NewHash().Bytes(),               // fee
					evmutils.NewHash().Bytes(),
				}, // requestID
					[]byte{}),
				Topics:      []common.Hash{{}, jb.ExternalIDEncodeBytesToTopic()}, // jobID BYTES
				TxHash:      evmutils.NewHash(),
				BlockNumber: 10,
				BlockHash:   evmutils.NewHash(),
			}
			vars = map[string]any{
				"jobSpec": map[string]any{
					"databaseID":    jb.ID,
					"externalJobID": jb.ExternalJobID,
					"name":          jb.Name.ValueOrZero(),
					"publicKey":     jb.VRFSpec.PublicKey[:],
					"evmChainID":    jb.VRFSpec.EVMChainID.String(),
				},
				"jobRun": map[string]any{
					"meta":           meta,
					"logBlockHash":   testLog.BlockHash[:],
					"logBlockNumber": testLog.BlockNumber,
					"logTxHash":      testLog.TxHash,
					"logTopics":      testLog.Topics,
					"logData":        testLog.Data,
				},
			}
		} else {
			vars = map[string]any{
				"jobRun": map[string]any{
					"meta": meta,
				},
			}
		}
		runID, _, err = app.pipelineRunner.ExecuteAndInsertFinishedRun(ctx, *jb.PipelineSpec, pipeline.NewVarsFrom(vars), saveTasks)
	}
	return runID, err
}
```

**File:** core/services/job/orm.go (L167-179)
```go
// ErrJobTypeRemoved is returned when attempting to create a job whose type has
// been permanently removed from this node.
var ErrJobTypeRemoved = fmt.Errorf("job type has been removed and is no longer supported: %w", stderrors.ErrUnsupported)

// CreateJob creates the job, and it's associated spec record.
// Expects an unmarshalled job spec as the jb argument i.e. output from ValidatedXX.
// Scans all persisted records back into jb
func (o *orm) CreateJob(ctx context.Context, jb *Job) error {
	// Permanently removed job types: reject all new submissions regardless of
	// which code path reaches here (REST API, GraphQL, feeds manager, etc.).
	if jb.Type == DirectRequest || jb.Type == FluxMonitor || jb.Type == Webhook {
		return fmt.Errorf("cannot create job of type %q: %w", jb.Type, ErrJobTypeRemoved)
	}
```

**File:** core/services/job/runner_integration_test.go (L836-849)
```go
	job, _ := cltest.MustInsertWebhookSpec(t, app.GetDB(), jobUUID)

	runBody := cltest.MustJSONMarshal(t, eiRequest)
	headers := map[string]string{
		static.ExternalInitiatorAccessKeyHeader: eia.AccessKey,
		static.ExternalInitiatorSecretHeader:    eia.Secret,
	}
	url := app.Server.URL + "/v2/jobs/" + jobUUID.String() + "/runs"
	resp, cleanup := cltest.UnauthenticatedPost(t, url, bytes.NewBufferString(runBody), headers)
	defer cleanup()
	cltest.AssertServerResponse(t, resp, http.StatusUnprocessableEntity)
	cltest.AssertCountStays(t, app.GetDB(), "pipeline_runs", 0)

	cltest.DeleteJobViaWeb(t, app, job.ID)
```

**File:** core/web/pipeline_runs_controller_test.go (L51-72)
```go
func TestPipelineRunsController_RunExistingWebhookJobRejected(t *testing.T) {
	t.Parallel()

	ethClient := cltest.NewEthMocksWithStartupAssertions(t)
	ethClient.On("BalanceAt", mock.Anything, mock.Anything, mock.Anything).Maybe().Return(big.NewInt(0), nil)
	cfg := configtest.NewGeneralConfig(t, func(c *chainlink.Config, s *chainlink.Secrets) {
		c.JobPipeline.HTTPRequest.DefaultTimeout = commonconfig.MustNewDuration(2 * time.Second)
	})

	app := cltest.NewApplicationWithConfig(t, cfg, ethClient)
	require.NoError(t, app.Start(t.Context()))

	jobUUID := uuid.New()
	cltest.MustInsertWebhookSpec(t, app.GetDB(), jobUUID)

	client := app.NewHTTPClient(nil)
	body := strings.NewReader(`{"data":{"result":"123.45"}}`)
	response, cleanup := client.Post("/v2/jobs/"+jobUUID.String()+"/runs", body)
	defer cleanup()
	cltest.AssertServerResponse(t, response, http.StatusUnprocessableEntity)
	require.Contains(t, string(cltest.ParseResponseBody(t, response)), "webhook")
}
```
