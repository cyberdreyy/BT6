### Title
HTTP Trigger requests are not rate-limited before expensive JWT signature verification, enabling unauthenticated DoS - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The Chainlink Gateway's HTTP Trigger user-facing path performs computationally expensive JWT/ECDSA signature verification (`authorizeRequest`) *before* consuming any per-workflow rate-limit token (`checkRateLimit`). When authorization fails, `checkRateLimit` is never invoked, so an unauthenticated caller can send unlimited garbage-signed requests against any known `workflowID` and force the gateway to repeatedly perform structural validation, workflow lookup, and ECDSA public-key recovery/JWT parsing with zero throttling cost, mirroring the "precompile fails to charge gas before erroring" class of DOS in the referenced report.

### Finding Description
`HandleUserTriggerRequest` processes an inbound, user-supplied `workflows.execute` request in this fixed order: [1](#0-0) 

1. `validatedTriggerRequest` — JSON parsing/structure validation.
2. `resolveWorkflowID` — map lookup (cheap).
3. `authorizeRequest` — calls `workflowMetadataHandler.Authorize`, which performs `utils.VerifyRequestJWT`: base64-decoding the signature, `crypto.SigToPub`/ECDSA public-key recovery (`GetSignersEthAddress`), full `jwt.ParseWithClaims` parsing/validation, and a digest recomputation over the whole request body. [2](#0-1) [3](#0-2) 
4. `checkRateLimit` — the only place the per-workflow-owner `userRateLimiter` token is consumed. [4](#0-3) 

Critically, if `authorizeRequest` returns an error (e.g., an invalid signature, wrong signer, or replayed JWT), `HandleUserTriggerRequest` returns immediately at line 100-102 and `checkRateLimit` is never reached: [5](#0-4) 

The only earlier gate on this path is a coarse, size-based body limiter at the HTTP layer (`MaxRequestBytesLimiter`), not a per-request or per-sender rate limiter: [6](#0-5) 

Unlike the node-facing `HandleNodeMessage` path, which enforces `globalNodeRateLimiter`/`perNodeRateLimiters` before doing any real work, the user-facing `HandleJSONRPCUserMessage` → `HandleUserTriggerRequest` path has no equivalent pre-authorization throttle: [7](#0-6) [8](#0-7) 

The `workflowID` needed to target this flow is not secret — it is resolvable via public workflow selectors (`workflowOwner`/`workflowName`/`workflowTag`) that are gossiped/synced via `WorkflowMetadataHandler`, so a workflowID is a fairly discoverable public value, not requiring insider knowledge.

### Impact Explanation
An unauthenticated remote client can repeatedly submit `workflows.execute` requests carrying a syntactically valid JWT (any signature, since it only needs to parse) against a known/discovered `workflowID`, forcing the gateway to run full JWT parsing and ECDSA signature recovery on every request, with the "cost" (the per-workflow rate limiter) never actually charged because it sits after the authorization check. This is analogous to the report's core defect: expensive work happens before the resource is "charged," and any error path escapes the charge entirely — enabling resource exhaustion/DoS against gateway nodes without needing valid credentials. Compared to a single ECDSA precompile call in the EVM analog, here the attack is even easier to reach because it requires no on-chain gas and no valid capability node/DON identity — just an HTTP POST to the public gateway endpoint.

### Likelihood Explanation
Likelihood is high: the endpoint is explicitly the internet-facing "HTTP Trigger" ingress meant for external callers (`HandleJSONRPCUserMessage`), requires no authentication to reach the expensive `authorizeRequest` code path, and the only gate before it is a byte-size limiter, not a request-rate limiter. `workflowID`/selectors used to target the flow are discoverable through normal workflow-selector resolution rather than secret.

### Recommendation
Consume (or pre-check) the per-workflow (and/or a global/per-sender) rate-limit token before performing JWT parsing/signature verification in `authorizeRequest`, or add a coarse pre-authorization rate limiter (analogous to `globalNodeRateLimiter`/`perNodeRateLimiters` on the node path) ahead of `HandleUserTriggerRequest`'s expensive work. Ensure the rate limiter is charged on both the failure and success paths of `authorizeRequest`, mirroring the report's recommendation to "charge gas before erroring."

### Proof of Concept
1. Resolve or guess a valid `workflowID` for a deployed CRE workflow (workflow selectors/metadata are synced and not treated as secret).
2. Repeatedly POST `workflows.execute` JSON-RPC requests to the gateway's public HTTP endpoint with a syntactically valid but incorrectly-signed/garbage JWT in `req.Auth`, using that `workflowID`.
3. Each request reaches `httpTriggerHandler.authorizeRequest` → `WorkflowMetadataHandler.Authorize` → `utils.VerifyRequestJWT`, performing full ECDSA public-key recovery and JWT claim parsing, then fails and returns before `checkRateLimit` is ever invoked [5](#0-4) .
4. Because the per-workflow rate limiter is never consumed on this failure path, the attacker can flood the gateway with such requests at line rate (bounded only by network/connection limits), consuming CPU on JWT/ECDSA operations per request with no rate-limit backpressure.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L371-396)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-90)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}
```

**File:** core/utils/jwt.go (L246-266)
```go
	signedString, signature, err := splitToken(tokenString)
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	decodedSignature, err := base64.RawURLEncoding.DecodeString(signature)
	if err != nil {
		return nil, gethcommon.Address{}, fmt.Errorf("signature segment is not valid base64url: %w", err)
	}
	pubKey, err := GetSignersEthAddress([]byte(signedString), decodedSignature)
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	verifiedToken, err := jwt.ParseWithClaims(tokenString, &JWTClaims{}, func(token *jwt.Token) (any, error) {
		if token.Method.Alg() != EthereumSigningMethod.Alg() {
			return nil, fmt.Errorf("unsupported JWT 'alg': '%s'. Expected '%s'", token.Method.Alg(), EthereumSigningMethod.Alg())
		}
		if _, ok := token.Method.(*SigningMethodEth); !ok {
			return nil, jwt.ErrSignatureInvalid
		}
		return pubKey, nil
	})
```

**File:** core/services/gateway/network/httpserver.go (L196-209)
```go
	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		msg := "Failed to get request size limit"
		s.lggr.Errorw(msg, "err", err)
		http.Error(w, msg, http.StatusInternalServerError)
		return
	}
	source := http.MaxBytesReader(nil, r.Body, int64(maxRequestBytes))
	rawMessage, err := io.ReadAll(source)
	if err != nil {
		s.lggr.Error("error reading request", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L238-254)
```go
func (h *gatewayHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	if resp.ID == "" {
		return fmt.Errorf("received response with empty request ID from node %s", nodeAddr)
	}
	h.lggr.Debugw("handling incoming node message", "requestID", resp.ID, "nodeAddr", nodeAddr)
	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementCapabilityNodeThrottled(ctx, nodeAddr, h.lggr)
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementGlobalThrottled(ctx, h.lggr)
		return errors.New("global rate limit exceeded")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L391-401)
```go
func (h *gatewayHandler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback handlers.Callback) error {
	h.metrics.IncrementTriggerRequestCount(ctx, h.lggr)
	err := h.triggerHandler.HandleUserTriggerRequest(ctx, &req, callback, time.Now())
	if err != nil {
		h.lggr.Errorw("failed to handle user trigger request", "requestID",
			req.ID, "err", err)
		// error response is sent to the response channel by the trigger handler
		// so return nil after logging
	}
	return nil
}
```
