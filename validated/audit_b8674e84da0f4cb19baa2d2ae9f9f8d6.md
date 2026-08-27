## Analog Found: Unauthenticated pipeline-run resume endpoint accepts arbitrary attacker-controlled task results

### Title
Unauthorized job-run result injection via unauthenticated `/v2/resume/:runID` endpoint - (File: core/web/pipeline_runs_controller.go)

### Summary
The Nexus report describes `fallback()` being reachable by anyone directly, bypassing the entrypoint/signature-validation path that all other privileged state-changing functions go through. The direct chainlink-core analog is the `PipelineRunsController.Resume` handler, which is mounted on a completely unauthenticated router group and resumes a pipeline run (i.e., completes an async task and injects a caller-supplied result) using only a UUID as the "authorization" mechanism, with no signature, token, or session check.

### Finding Description
The route is registered outside any authentication middleware chain: [1](#0-0) 

Compare this to every other v2 route, which is wrapped in `auth.Authenticate(...)` with token/session/external-initiator checks: [2](#0-1) [3](#0-2) 

The `Resume` handler itself performs no authorization check at all — it parses the `runID` path parameter as a UUID, decodes an attacker-supplied JSON body into a `pipeline.ResumeRequest`, and immediately calls `App.ResumeJobV2` with that data: [4](#0-3) 

The only "access control" is the unguessability of the `runID` UUID (task ID) — there is no verification that the caller is the specific external adapter/bridge that was actually given that task ID, no HMAC/secret binding the response to the outstanding task, and the audit log entry name (`UnauthedRunResumed`) confirms this path was deliberately left outside the authentication framework. This mirrors exactly the fallback() bug class: a sensitive, state-mutating entry point that bypasses the node's standard authorization pipeline (session/token/external-initiator auth used everywhere else) and instead relies solely on an alternate, weaker check (a bearer-like UUID) baked into the caller-supplied path.

### Impact Explanation
Any actor who obtains or guesses a pending task's `runID` (e.g., via network observation of a bridge/external-adapter callback URL, log exposure, or a compromised/misbehaving bridge integration point) can directly call this endpoint with an arbitrary JSON result body and force the pipeline run to resume with attacker-controlled data. Because this is the same mechanism external adapters use to complete VRF/bridge/HTTP tasks, an attacker can inject forged task results into a job run, potentially corrupting the on-chain outcome the job produces (price data, VRF fulfillment metadata, etc.) or completing runs prematurely/incorrectly — a direct instance of "unauthorized job run" / "request impersonation" per the acceptance criteria.

### Likelihood Explanation
Exploitability requires knowledge of a live `runID` UUID for a task that is currently paused awaiting resumption. Since UUIDs (122 bits of entropy) are not brute-forceable, the realistic threat model is disclosure or interception of the ID from logs, network traffic to the external adapter, or a malicious/compromised bridge — this is not a "malicious peer/network-layer" issue excluded by the rules, since the reachable surface is the node's own internet-facing HTTP API without any authentication layer, matching the fallback()-analog requirement of "unrestricted direct call bypassing the intended authorization path."

### Recommendation
Bind the resume operation to an authenticated caller in addition to the UUID (e.g., require the External Initiator credentials of the bridge that owns the task, or a per-task HMAC secret issued at task-creation time and validated in `Resume`), rather than relying purely on the secrecy of the `runID` value.

### Proof of Concept
1. Create/observe a job that has an in-flight pipeline task waiting on resumption (any bridge/external-adapter-backed task), obtaining or leaking the resulting `runID`.
2. Without any session cookie, API token, or EI credentials, send:
   `PATCH /v2/resume/<runID>` with body `{"error": null, "data": {<attacker-controlled result>}}`
3. The request hits `PipelineRunsController.Resume` via the unauthenticated router group and is passed straight to `App.ResumeJobV2`, completing the task with attacker-supplied data — no authentication check is performed anywhere in this path. [5](#0-4) [6](#0-5)

### Citations

**File:** core/web/router.go (L238-244)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

```

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
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
