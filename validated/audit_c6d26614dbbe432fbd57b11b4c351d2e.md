### Title
Unbounded cryptographic authorization work before rate limiting in HTTP trigger handler - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`httpTriggerHandler.HandleUserTriggerRequest` performs workflow authorization (JWT/signature verification via `authorizeRequest`) **before** applying the per-workflow rate limit check (`checkRateLimit`). This mirrors the reported `handleNewAuction` pattern where expensive, asynchronous work is triggered by unauthenticated/unprivileged input before any throttling occurs, creating a DoS vector against a request-handling entrypoint.

### Finding Description
In `HandleUserTriggerRequest`, the call order is:
1. `validatedTriggerRequest` (parsing/basic validation)
2. `resolveWorkflowID`
3. `authorizeRequest` — performs cryptographic authorization (JWT/ECDSA-based key check) via `h.workflowMetadataHandler.Authorize`
4. `checkRateLimit` — the per-workflow-owner rate limiter (`userRateLimiter`) [1](#0-0) 

Because `authorizeRequest` runs before `checkRateLimit`, any client reaching this handler can force the node to perform cryptographic signature verification work for every submitted request — including requests with deliberately invalid signatures/workflow references — without that cost being counted against, or bounded by, the rate limiter. Only requests that pass authorization are subsequently subjected to rate limiting. [2](#0-1) 

This is analogous to the reported `handleNewAuction` issue where significant, asynchronous work (including a signed reply) is triggered before any throttling/quota check, allowing a client to force the service to repeatedly perform expensive operations ("user might force the router to sign the same message multiple times").

### Impact Explanation
An unprivileged client can submit a high volume of trigger requests with syntactically valid but cryptographically invalid parameters (bad signatures, non-existent workflow selectors that still pass basic field validation) to force repeated expensive authorization computation on the gateway, since this path isn't gated by `userRateLimiter`. If sustained, this could degrade gateway responsiveness for legitimate workflow owners (partial denial of service). Impact is bounded to availability degradation of the HTTP trigger capability; there is no code path here shown for fund movement or key disclosure.

### Likelihood Explanation
Reachable directly from any client capable of submitting an HTTP trigger request through the gateway's `HandleUserTriggerRequest` path; no special privileges or node compromise are required. Likelihood depends on whether an outer, request-level rate limiter (e.g., a global ingress-level limiter in the gateway's message dispatch) already throttles inbound requests before they reach `HandleUserTriggerRequest`; I was not able to fully verify the top-level `HandleUserMessage`/dispatch code path in the time available, so this should be confirmed against the actual entrypoint before or after the JSON-RPC parsing stage.

### Recommendation
- Move `checkRateLimit` (or an equivalent lightweight per-source/per-IP quota check) ahead of `authorizeRequest` so that expensive cryptographic verification is only performed for requests within budget.
- Consider a cheap pre-check (e.g., a global or per-source token bucket) applied uniformly regardless of authorization outcome, similar to how `gatewayHandler.send` explicitly comments on consuming rate-limit tokens only after cheap validation to prevent draining shared resources [3](#0-2) .
- Add metrics/alerts on authorization failure rate to detect flooding attempts.

### Proof of Concept
1. Send a high-volume stream of `workflows.execute` JSON-RPC requests to the gateway's HTTP trigger endpoint, each with a distinct `id` (to bypass request-ID collision checks), a valid-looking `workflowID`/`workflowOwner` selector format, but an invalid/garbage authorization signature.
2. Each request passes `validatedTriggerRequest` and `resolveWorkflowID` (if a real workflow ID is reused or brute-forced) and reaches `authorizeRequest`, which performs signature verification via `workflowMetadataHandler.Authorize` — this cost is incurred for every request.
3. Because `checkRateLimit` only executes after `authorizeRequest` succeeds, failed-authorization requests are never throttled, allowing sustained repeated invocation of the authorization routine.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-396)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}

func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L326-334)
```go
	// We don't have access to the org here, so this will fall back to the environment default (=false).
	// That's appropriate because all fields set on the request come from untrusted nodes.
	// The capability separately applies an org-specific check.

	// Note: we intentionally consume the rate-limit after instantiating the client so that a malicious user
	// can't send requests with invalid mtls credentials and thus cheaply consume global tokens.
	if !h.mtlsRequestRateLimiter.Allow(ctx) {
		return nil, fmt.Errorf("global mtls request rate limit exceeded: %w", network.ErrBlockedRequest)
	}
```
