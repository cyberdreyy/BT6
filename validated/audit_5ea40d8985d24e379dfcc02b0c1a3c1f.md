### Title
Unauthenticated `/v2/resume/:runID` endpoint allows unauthorized completion of pending async job runs - ([File: core/web/pipeline_runs_controller.go])

### Summary
The external-report's bug class — pending Chainlink Any-API/external-adapter requests that cannot be safely cancelled/resumed by an authorized party, leaving a race window for hijack or permanent stall — maps to Chainlink node's own async-callback mechanism for `bridge` tasks: the `PATCH /v2/resume/:runID` route. This route is intentionally registered in the **unauthenticated** router group and accepts arbitrary result data for any `taskID` UUID it is given, relying solely on the unguessability of the UUID as a bearer credential.

### Finding Description
The v2 router explicitly mounts the resume endpoint on the unauthenticated group, separate from the token/session/external-initiator authenticated group: [1](#0-0) 

The handler parses `runID` as a UUID (the task run ID) and, without any authentication or authorization check, decodes an arbitrary JSON body into a `pipeline.ResumeRequest`, converts it into a task result, and calls `App.ResumeJobV2` to complete/resume the suspended pipeline task with that data: [2](#0-1) 

This is the same "callback to complete a pending async request" pattern described in the report (`ChainlinkClient:cancelChainlinkRequest` + callback to reinitiate/complete). Here, instead of a smart contract callback, it's a bare HTTP `PATCH` gated only by knowledge of a UUID (`taskID`). Notably, the code even logs the action as `audit.UnauthedRunResumed`, acknowledging in its own naming that this endpoint is deliberately unauthenticated: [3](#0-2) 

The `ResumeRun`/`ResumeJobV2` flow is the standard path used by async bridge/external-adapter tasks (`X-Chainlink-Pending` pattern, documented in the CHANGELOG) to signal completion of a suspended pipeline run: [4](#0-3) 

Because there is no authentication tied to the caller (no verification that the caller is the specific external adapter that received the original callback URL, nor any secret/HMAC/token check beyond the raw UUID), **anyone who can reach the node's `/v2/resume/:runID` endpoint and who obtains or guesses the `taskID`** can complete, poison, or race the completion of that pending job run with attacker-controlled data (`value`/`error` fields of `ResumeRequest`). This directly parallels the external report's concern that a stuck/pending external-adapter request creates a window in which the wrong party — or a third party — can drive the request's outcome, since there is no mechanism binding the resume action to the true, expected caller beyond an ID that is transmitted in plaintext over the network (in the `responseURL` sent to the external adapter) and stored/logged in various places (e.g., HTTP client logs, adapter logs, potentially error responses).

### Impact Explanation
If a `taskID` (UUID) is disclosed or guessed by an unprivileged actor — e.g., via network logs, external-adapter logs, timing/enumeration, or an intercepted request to a misconfigured/compromised bridge — that actor can:
- Complete a stuck/pending async task with attacker-chosen data, directly influencing job run output (analogous to influencing "how many boxes to mint" in the reported bug), or
- Race the legitimate external adapter response to inject a different result before the real one arrives, causing cross-caller response confusion for the pipeline run.

This can lead to unauthorized manipulation of job run results, which for jobs feeding on-chain transactions (e.g. `ethtx`) can translate into fund movement or corrupted on-chain state — matching the "unauthorized job run" and "cross-user response confusion" categories called out as acceptable analogs.

### Likelihood Explanation
The endpoint requires only knowledge of the task's UUID, no credentials. While a UUID is not trivially guessable, it is not treated as a cryptographic secret in the codebase (no HMAC/signature check, no scoping to the bridge that issued it, no expiry check enforced at this layer), and it must be transmitted to third-party external adapters over the network for the async pattern to function at all — meaning it is not confidential to the node. Any leak (adapter-side logging, network capture, adapter compromise, SSRF, or a slow/hung request being retried) is sufficient to exploit this. The endpoint is exposed on the node's web server without gateway allowlisting.

### Recommendation
- Bind resume requests to a per-request, single-use, cryptographically random secret (not just the task UUID) or require an HMAC/signature over the response body using a per-run secret known only to the node and the specific bridge/EA.
- Enforce that only the bridge/EA associated with the originating task can resume it (validate a shared secret configured per bridge, not just possession of a UUID).
- Rate-limit and log/alert on repeated or unexpected resume attempts for a given `taskID`.
- Consider requiring the resume request to arrive from an IP/host matching the bridge's registered URL, defense-in-depth.

### Proof of Concept
Not independently executable from static analysis alone; conceptually:
1. A `bridge async=true` task is created, and the node sends a request to the external adapter with `responseURL: http://<node>:6688/v2/resume/<taskID>` (see async test fixture referencing this exact route pattern): [5](#0-4) 
2. Before (or instead of) the legitimate adapter's callback, an attacker who has obtained `<taskID>` sends:
   `PATCH /v2/resume/<taskID>` with body `{"value": "<attacker-controlled>"}` (or `{"error": "..."}`).
3. Because `/v2/resume/:runID` is in the unauthenticated router group and `Resume()` performs no ownership/secret check beyond UUID parsing, `ResumeJobV2` completes the pending task with the attacker's data. [6](#0-5) [7](#0-6) 

**Uncertainty note:** I could not fully confirm within the available index whether `taskID` values are exposed anywhere in logs, error responses, or other observable channels reachable by an unprivileged actor in this codebase snapshot (the `ResponseURL` construction logic in `core/services/pipeline/task.bridge.go` was located but not read in full due to iteration limits). Confirming a concrete leak/guess vector for `taskID` would strengthen the likelihood assessment; a Devin session with full file access could verify this by reading `core/services/pipeline/task.bridge.go` end-to-end and any related audit/log statements.

### Citations

**File:** core/web/router.go (L238-248)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/pipeline_runs_controller.go (L130-159)
```go
// Resume finishes a task and resumes the pipeline run.
// Example:
// "PATCH <application>/jobs/:ID/runs/:runID"
func (prc *PipelineRunsController) Resume(c *gin.Context) {
	taskID, err := uuid.Parse(c.Param("runID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	rr := pipeline.ResumeRequest{}
	decoder := json.NewDecoder(c.Request.Body)
	err = errors.Wrap(decoder.Decode(&rr), "failed to unmarshal JSON body")
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	result, err := rr.ToResult()
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	if err := prc.App.ResumeJobV2(c.Request.Context(), taskID, result); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	prc.App.GetAuditLogger().Audit(audit.UnauthedRunResumed, map[string]any{"runID": c.Param("runID")})
	c.Status(http.StatusOK)
```

**File:** core/services/pipeline/runner.go (L32-39)
```go
type Runner interface {
	services.Service

	// Run is a blocking call that will execute the run until no further progress can be made.
	// If `incomplete` is true, the run is only partially complete and is suspended, awaiting to be resumed when more data comes in.
	// Note that `saveSuccessfulTaskRuns` value is ignored if the run contains async tasks.
	Run(ctx context.Context, run *Run, saveSuccessfulTaskRuns bool, fn func(tx sqlutil.DataSource) error) (incomplete bool, err error)
	ResumeRun(ctx context.Context, taskID uuid.UUID, value any, err error) error
```

**File:** core/services/pipeline/runner_test.go (L780-791)
```go
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var reqBody adapterRequest
		payload, err := io.ReadAll(r.Body)
		require.NoError(t, err)
		defer r.Body.Close()
		err = json.Unmarshal(payload, &reqBody)
		require.NoError(t, err)
		require.Contains(t, reqBody.ResponseURL, "http://localhost:6688/v2/resume/")
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Chainlink-Pending", "true")
		response := map[string]any{}
		require.NoError(t, json.NewEncoder(w).Encode(response))
```
