## Analysis

I examined the chainlink node's unprivileged-facing surfaces (web API auth middleware, RBAC role gating, external-initiator token handling, and the internet-facing gateway) to look for a bug-class analog to the "endpoints not verifying approval status before allowing sensitive actions" pattern from the report. The strongest analog is the completely unauthenticated `PATCH /v2/resume/:runID` route, which is registered outside of any auth middleware group and lets an unprivileged caller resume/finish arbitrary pipeline task runs by GUID alone.

### Title
Unauthenticated Pipeline Run Resume Endpoint Allows Arbitrary Task Result Injection - (File: core/web/router.go)

### Summary
The route `PATCH /v2/resume/:runID` is registered on the `unauthedv2` router group, which has no authentication middleware attached at all, unlike every other `/v2` route which requires session, token, or external-initiator authentication. [1](#0-0) 

### Finding Description
`v2Routes` splits routes into `unauthedv2` (no auth) and `authv2` (wrapped in `auth.Authenticate` with session/token methods). `PipelineRunsController.Resume` is mounted on `unauthedv2`: [1](#0-0) 

The handler itself performs no additional caller verification — it parses the `runID` as a UUID, decodes an arbitrary JSON body into a `pipeline.ResumeRequest`, and calls `App.ResumeJobV2` directly: [2](#0-1) 

This is conceptually the same bug class as the KYC report: an action that should require a verified caller/entitlement (in the report's case, "KYC approved"; here, "possession of the correct pending task/run") is instead exposed on an endpoint that performs no server-side authorization check — protection is implicit/absent rather than enforced. The only "secret" here is the UUID `runID` (task ID), which functions like a bearer token; if it is guessable, logged, or otherwise disclosed (e.g., via bridge/adapter logs, error messages, or third-party systems that receive it), any unauthenticated actor can submit an arbitrary resume payload for that task ID and inject a controlled result (or error) into a live pipeline run.

The audit log call `audit.UnauthedRunResumed` at line 158 indicates the developers were aware this route is intentionally unauthenticated, treating the UUID as sufficient authorization. This is a deliberate design tradeoff (external adapters need to callback with task results without holding node credentials), not a coding oversight — so whether it counts as a "vulnerability" hinges entirely on whether `runID` values are treated as confidential secrets everywhere they are generated/transmitted, which I could not fully verify from the indexed code (adapter integration code and where `runID` is disclosed to external systems is outside what I could inspect).

### Impact Explanation
If a `runID` leaks (e.g., via bridge/adapter request logs, third-party service logs, browser history, or a compromised external adapter), an attacker can forge or replay a `ResumeRequest` to inject a falsified result into that pipeline run, potentially affecting job outcomes that feed on-chain data or fund-moving OCR/direct-request jobs. This maps to the "unauthorized job run" / "cross-user response confusion" category in the validation rubric.

### Likelihood Explanation
Likelihood is moderate-to-low: exploitation requires the attacker to first obtain a valid, still-pending `runID` (a UUID), which is not brute-forceable in practice. This differs from the original report, where the entire "approved" status could be forged client-side without any external secret. Here the confidentiality of the UUID is the sole control, and I was not able to confirm within this scan whether `runID` values are ever exposed to lower-trust parties (e.g., via HTTP responses to external adapters, logs, or the gateway).

### Recommendation
- Confirm that `runID` (task UUID) is never logged or exposed to any component with less trust than the node itself; treat it as a bearer secret with sufficient entropy (128-bit UUIDv4 already provides this if not otherwise leaked).
- Consider requiring an additional shared secret (e.g., bridge outgoing token, already used for bridge callbacks) for the resume callback rather than relying solely on the run UUID, aligning `/v2/resume/:runID` with how bridge webhook callbacks are authenticated elsewhere (`AuthenticateExternalInitiator` in `core/web/auth/auth.go`). [3](#0-2) 
- Rate-limit and monitor `/v2/resume/:runID` for repeated/failed UUID guesses.

### Proof of Concept
1. Obtain (or guess/leak) a pending `runID` associated with a suspended pipeline task (e.g., from adapter logs or a compromised external adapter integration).
2. Send an unauthenticated request:
```
PATCH /v2/resume/<runID>
Content-Type: application/json

{"value": "<attacker-controlled-result>"}
```
3. The node processes this with no session, API token, or external-initiator credential check, calling `App.ResumeJobV2` with the attacker-supplied result, as confirmed by the route registration and handler code cited above.

**Caveat**: I could not fully verify within the indexed codebase whether `runID` is exposed to any untrusted party in normal operation (this would require inspecting bridge/adapter response-handling code and deployment configs not covered by the index). Without that confirmation, this should be treated as a plausible-but-unconfirmed analog rather than a proven exploit chain — a Devin session with full repo/file access would be needed to trace every code path that emits or logs `runID` values before treating this as a confirmed critical finding.

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

**File:** core/web/auth/auth.go (L119-151)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
```
