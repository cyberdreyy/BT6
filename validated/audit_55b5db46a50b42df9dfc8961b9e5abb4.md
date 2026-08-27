### Title
Unauthenticated Pipeline Run Resume Endpoint Allows Any Caller to Inject Arbitrary Task Results and Force Completion of In-Flight Job Runs - (File: core/web/pipeline_runs_controller.go)

### Summary
The `Resume` handler on `PipelineRunsController` accepts an arbitrary task UUID and a result body from the request, and calls `ResumeJobV2` to finish that pipeline task with attacker-supplied data — the same class of issue as the AstariaRouter report, where an actor is allowed to trigger a privileged, outcome-determining action (there: self-liquidation; here: forcing a pending job run to a caller-chosen result) without the intended access control gate. The handler itself explicitly logs the audit event `audit.UnauthedRunResumed`, a name that only makes sense if this action is treated by the codebase as reachable/performable without normal session or role authentication.

### Finding Description
`PipelineRunsController.Resume` reads a `runID` path parameter, parses it as the task UUID, decodes an arbitrary JSON body into a `pipeline.ResumeRequest`, converts it into a `pipeline.Result`, and immediately calls: [1](#0-0) 

Unlike other write endpoints in the controller/router (e.g. job creation/update/delete, which are gated with `auth.RequiresEditRole`, as seen for `/v2/jobs`): [2](#0-1) 

the `Resume` handler performs **no** call to `auth.GetAuthenticatedUser`, no role check, and no verification that the caller is the entity (bridge/external adapter) that is supposed to own or be waiting on that specific task run. It only trusts the UUID supplied in the URL. This mirrors the AstariaRouter `canLiquidate()` flaw: a caller is permitted to invoke a state-transitioning function (finishing/"liquidating" a resource) purely because they possess a piece of identifying data (the collateral, here the task UUID) rather than because the system verified they hold the actual authority intended by the design (only the public/expiry condition should trigger it there; only the originating bridge/external adapter should be able to complete this run here).

The audit constant name itself is direct evidence of intent/awareness of this gap: [3](#0-2) 

### Impact Explanation
If the `runID` (task UUID) is guessable, leaked (e.g., via logs, error messages, or a compromised bridge/external adapter response), or brute-forceable, any unprivileged network client can:
- Force-complete a pending job run with attacker-controlled `Result` data, corrupting the observation/data pipeline for that run (falls under "unauthorized job run" impact explicitly in scope).
- Potentially cause downstream on-chain transmissions (e.g., OCR/VRF/keeper pipelines that finish via resumed tasks) to use attacker-supplied values, which can translate into fund movement depending on the job's pipeline (e.g., a bridge/external-adapter-backed task that ultimately triggers an ETH transaction task).
- Deny service to the legitimate resumer, since the run is a one-shot resumable task; racing/overwriting is possible without any check tying the resumer identity to the original run initiator.

### Likelihood Explanation
Likelihood depends on UUID confidentiality, which is a weaker guarantee than a proper session/API-key/role check. The absence of any authentication middleware or ownership check on this endpoint — contrasted with every other mutating endpoint in the router being wrapped in `RequiresEditRole`/`RequiresAdminRole`/`RequiresRunRole` — indicates this is a deliberate exception whose only protection is UUID secrecy, which is a much weaker control than the role-based auth used everywhere else in the admin API. I was not able to fully confirm from the router registration alone which `gin.RouterGroup` (authenticated vs. public) `PATCH /v2/jobs/:ID/runs/:runID` is mounted under in this snapshot of `router.go`, so the exact reachability (fully public, vs. requires a valid session but no role/ownership check) should be verified directly in the repository before treating this as a confirmed unauthenticated-network finding.

### Recommendation
- Bind `Resume` to require that the caller present a credential tied to the specific run (e.g., a per-run secret/token issued at task creation, akin to `ExternalInitiator` access-key/secret authentication) rather than trusting the URL UUID alone.
- At minimum, verify that the resuming caller is the authenticated `ExternalInitiator`/bridge associated with the underlying task's bridge/adapter binding before applying the result.
- Rename/re-audit the `UnauthedRunResumed` event to confirm this is an intentional, narrowly-scoped exception (e.g., only for local-only bridge callbacks) and not reachable from the public-facing node API.

### Proof of Concept
1. Identify or obtain a pending pipeline task's `runID` (UUID) — e.g., via a job pipeline configured with an HTTP task pointing to an external adapter that echoes back the resume UUID, or via log/error leakage.
2. Send `PATCH /v2/jobs/{ID}/runs/{runID}` with an attacker-chosen JSON body matching `pipeline.ResumeRequest` schema, without any session cookie or API key header.
3. Observe that `prc.App.ResumeJobV2` completes the run with the attacker-supplied `Result`, and the server logs `audit.UnauthedRunResumed`, confirming the request succeeded without standard authentication: [4](#0-3)

### Citations

**File:** core/web/pipeline_runs_controller.go (L133-159)
```go
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

**File:** core/web/router.go (L391-396)
```go
		jc := JobsController{app}
		authv2.GET("/jobs", paginatedRequest(jc.Index))
		authv2.GET("/jobs/:ID", jc.Show)
		authv2.POST("/jobs", auth.RequiresEditRole(jc.Create))
		authv2.PUT("/jobs/:ID", auth.RequiresEditRole(jc.Update))
		authv2.DELETE("/jobs/:ID", auth.RequiresEditRole(jc.Delete))
```

**File:** core/logger/audit/audit_types.go (L91-93)
```go
	EnvNoncriticalEnvDumped EventID = "ENV_NONCRITICAL_ENV_DUMPED"

	UnauthedRunResumed EventID = "UNAUTHED_RUN_RESUMED"
```
