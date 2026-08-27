### Title
Manual job-run endpoint allows any authenticated user to trigger arbitrary job types (OCR/VRF/etc.) with no job-type restriction, gated only by a build flag rather than role/type authorization - (File: core/services/chainlink/application.go, core/web/pipeline_runs_controller.go)

### Summary
`PipelineRunsController.Create` accepts any int32 job ID from any authenticated user and forwards it directly to `Application.RunJobV2` without checking the job's `Type`. `RunJobV2` itself performs no job-type restriction (it happily executes OCR, VRF, or any job with a `PipelineSpec`); the only gate preventing this is a global `build.IsProd()` check that disables the feature entirely on "secure builds," not a role- or job-type-based authorization decision.

### Finding Description
The route handler `PipelineRunsController.Create` in `core/web/pipeline_runs_controller.go` only requires that the caller be an authenticated `isUser` (via `auth.GetAuthenticatedUser(c)`) and that the `:ID` path param parse as an `int32`: [1](#0-0) 

It then calls `prc.App.RunJobV2(ctx, jobID, nil)` unconditionally — there is no check on `jb.Type` in this handler to restrict execution to Webhook-style/manually-triggerable jobs.

The implementation, `ChainlinkApplication.RunJobV2` in `core/services/chainlink/application.go`, fetches the job by ID and runs its pipeline spec (or synthesizes a fake VRF log) for essentially any job type that has a pipeline spec, with no job-type allowlist: [2](#0-1) 

The only protection is a global build-time/environment gate: [3](#0-2) 

This is not a role- or job-type-based control — it is a single global flag checked before the function does anything else. The function comment itself acknowledges the design intent ("Only used for local testing, not supported by the UI"), confirming that no per-job-type authorization was implemented; the authors relied entirely on `build.IsProd()` to prevent misuse in production, not on validating that the job in question is a manually-triggerable type. Consequently, on any deployment where `build.IsProd()` evaluates false (non-"secure" builds, which is common for self-hosted/CE Chainlink node builds), any authenticated user (regardless of role granularity enforced elsewhere in the router) can POST `/v2/jobs/:ID/runs` for an arbitrary job ID — including an OCR or VRF job never intended to be manually triggered — and force `ExecuteAndInsertFinishedRun` to execute its pipeline, potentially causing unintended on-chain writes (e.g., re-submitting a VRF fulfillment via the synthesized fake log, or running an OCR job's pipeline tasks out of band).

### Impact Explanation
An attacker who only holds run-level API/session access can force execution of a job pipeline belonging to any job type (OCR, VRF, etc.) that was never designed to accept manual/webhook triggers, by simply guessing/enumerating small integer job IDs. Since pipeline tasks can include HTTP requests, bridge calls, and transaction-submitting tasks, this could result in unauthorized on-chain transactions or unintended external side effects — matching the "unauthorized job run / fund movement" impact class. The practical severity is bounded by the `build.IsProd()` gate: on official "secure" production builds the entire code path returns an error, so the exposure is limited to non-secure/dev builds and any environment where that build flag is not set as expected.

### Likelihood Explanation
Preconditions are low: only a valid session or API token with run-level access is needed, no admin/editor privileges required. The vulnerability is trivially repeatable (a single POST request with an arbitrary integer ID). Its real-world likelihood is directly tied to whether the deployed binary was built with the "secure build" flag causing `build.IsProd()` to return true; the repository index does not show the definition of `build.IsProd()`, so it is uncertain how this flag is set for standard release builds versus community/self-built binaries — this is a gap in what could be verified from the indexed code.

### Recommendation
Add an explicit job-type check inside `RunJobV2` (or in `PipelineRunsController.Create`) that rejects manual runs for job types not designed for it (e.g., restrict to `Webhook`/`Cron`/explicitly-flagged manually-triggerable types), independent of the `build.IsProd()` build-time gate, and enforce it via a proper role check for job type rather than relying solely on a global compile-time flag.

### Proof of Concept
1. Build the node without the "secure build" tag (so `build.IsProd()` returns false).
2. Create an OCR job (not Webhook) via the jobs API.
3. Authenticate as a run-role user (session or API token limited to run permissions).
4. POST `/v2/jobs/<OCR-job-id>/runs`.
5. Assert that `RunJobV2` executes the OCR job's pipeline spec and returns HTTP 200 with a pipeline run resource, demonstrating the manual trigger succeeded for a non-Webhook job type — contrast with expected behavior of a 422/403 rejection for job types not intended for manual triggering.
6. Additionally add a unit test for `RunJobV2` directly asserting it returns an error (not a run) when `jb.Type` is not in an explicit "manually triggerable" allowlist.

### Citations

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
