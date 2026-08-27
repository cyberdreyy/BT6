### Title
Unauthenticated fan-out request amplification via missing allowlist/rate-limit check in legacy WebAPI trigger handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.HandleLegacyUserMessage` function in the gateway's WebAPI capability handler is the entry point for legacy `web_api_trigger` requests submitted by external, unprivileged HTTP clients through `gateway.ProcessRequest`. Unlike the sibling v2 HTTP handler and the vault handler, this code path performs no allowlist or rate-limit check before accepting a request, storing a callback, and fanning the request out to every member node of the DON.

### Finding Description
`gateway.ProcessRequest` routes any externally-submitted "legacy" JSON-RPC request (identified by presence of `msg.Body.DonId`) directly to `h.HandleLegacyUserMessage(ctx, msg, callback)` after only basic structural validation (`msg.Validate()`), with no caller authentication/authorization performed at this layer: [1](#0-0) 

Inside `HandleLegacyUserMessage`, after checking payload well-formedness, timestamp freshness, and method name, the code contains an explicit acknowledgment that authorization is missing: [2](#0-1) 

The request is then unconditionally accepted — a callback is saved into the shared `savedCallbacks` map and forwarded to **every** member of `h.donConfig.Members`: [3](#0-2) 

This is analogous to the reported vault issue: an unprivileged actor can supply many cheap/dust requests (here, `web_api_trigger` messages) that get accepted without any check tying them to an authorized sender, workflow owner, or rate limit, and each one causes fan-out work (one `SendToNode` per DON member) plus a stored callback entry. The handler's own test file documents this gap explicitly as unresolved: [4](#0-3) 

By contrast, the newer HTTP capability handler (`v2/http_handler.go`) enforces `globalNodeRateLimiter`, `perNodeRateLimiters`, and a per-workflow-owner `userRateLimiter` before processing, and the vault handler (`handlers/vault/handler.go`) requires `requestProcessor.ProcessRequest` authorization before creating an active request: [5](#0-4) [6](#0-5) 

The `savedCallbacks` map does have a periodic pruning routine (`pruneCallbacks`), so it is not literally unbounded — it caps at `MaxSavedCallbacks` (default 20000) and evicts oldest entries every `CallbackPruneIntervalSec` (default 30s): [7](#0-6) 

However, the lack of allowlist/rate-limiting means an unprivileged client can, within each 30-second pruning window, repeatedly submit up to the map-size worth of trigger requests, each one causing the gateway to synchronously call `SendToNode` against every DON member. This is unauthenticated request amplification and resource consumption directly analogous to the report's "dust spam" DoS class, mapped onto the gateway's message-handling path rather than the vault contract.

### Impact Explanation
An unprivileged remote client (anyone able to reach the gateway's public HTTP endpoint) can flood the legacy `web_api_trigger` path to:
- Consume gateway CPU/memory via unauthenticated callback map churn and repeated sort/evict cycles in `pruneCallbacks`.
- Force fan-out `SendToNode` calls to every DON member node per request, amplifying a single cheap request into N node-directed messages, which can degrade node-to-gateway bandwidth/connections and potentially crowd out legitimate trigger traffic.
- Because there is no allowlist, this is not limited to workflow owners or known senders — any external caller who can reach the endpoint qualifies, unlike the properly-gated v2 and vault paths.

This is comparable to Medium severity: it causes resource-consumption degradation (not outright fund loss, since this is the node/gateway layer, not the vault), and legitimate operators/administrators would need to intervene (e.g., disable the legacy path or deploy the v2 handler) to remediate, similar to how the vault issue required a "settle debt" admin recovery action.

### Likelihood Explanation
High. No credentials, allowlist membership, or rate limiting are required to reach this code path — only network access to the gateway's HTTP endpoint and knowledge of the legacy JSON-RPC request/message format (which is documented in the codebase's own request/response types). The behavior is deterministic and repeatable.

### Recommendation
- Implement the allowlist and rate-limiting check referenced by the `// TODO: apply allowlist and rate-limiting here` comment in `HandleLegacyUserMessage` before accepting/storing a callback or fanning the request out to DON members, mirroring the `globalNodeRateLimiter`/`perNodeRateLimiters`/`userRateLimiter` pattern used in `v2/http_handler.go`, or the `requestProcessor.ProcessRequest` authorization used in `handlers/vault/handler.go`.
- Consider requiring a minimum-cost or authenticated sender identity for legacy trigger requests before performing the DON-wide fan-out, and add a per-sender quota to bound the number of outstanding `savedCallbacks` entries any single unauthenticated caller can create.
- Given the existing `defaultMaxSavedCallbacks`/pruning mechanism only bounds memory, add an explicit ceiling on fan-out rate per source to prevent request amplification against DON members.

### Proof of Concept
1. Deploy or run a gateway configured with the `capabilities` handler (`core/services/gateway/handlers/capabilities/handler.go`) serving a DON with N members.
2. As an unauthenticated client, repeatedly POST a well-formed legacy JSON-RPC request to the gateway's HTTP endpoint with `Body.DonId` set to the target DON, `Body.Method = "web_api_trigger"`, a valid non-empty `webapicap.TriggerRequestPayload` (fresh `Timestamp`), and a fresh `MessageId` each time — no allowlist membership, signature-based sender validation for this check, or rate-limit token is enforced by `HandleLegacyUserMessage` before it proceeds to `common.ValidatedRequestFromMessage` and fan-out.
3. Observe that each request results in an entry added to `savedCallbacks` and N calls to `don.SendToNode`, for as many requests as the attacker can send within a 30-second pruning window (up to `MaxSavedCallbacks`), demonstrating unauthenticated resource consumption and DON-directed fan-out amplification with no attacker cost beyond forming valid request bodies.

Note: I was not able to fully verify from the indexed code whether an additional signature-based sender authentication (separate from the allowlist/rate-limit TODO) is enforced somewhere upstream in `common.ValidatedRequestFromMessage` or `msg.Validate()`, since only summaries of those functions were available in the index — a Devin session with full repository access would be needed to confirm whether message-signature validation alone is sufficient to prevent this, or whether it only validates structural well-formedness (as the existing TODO and test comments strongly suggest).

### Citations

**File:** core/services/gateway/gateway.go (L250-273)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}

	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-334)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

	// If there are still too many callbacks, sort them by creation time and remove the oldest ones.
	maxSize := h.config.MaxSavedCallbacks
	var evicted int
	if len(h.savedCallbacks) > maxSize {
		type entry struct {
			id        string
			createdAt time.Time
		}
		entries := make([]entry, 0, len(h.savedCallbacks))
		for id, cb := range h.savedCallbacks {
			entries = append(entries, entry{id, cb.createdAt})
		}
		sort.Slice(entries, func(i, j int) bool {
			return entries[i].createdAt.Before(entries[j].createdAt)
		})
		// Trim to maxSize/2 to avoid sorting the list too frequently.
		for _, e := range entries[:len(entries)-maxSize/2] {
			delete(h.savedCallbacks, e.id)
			evicted++
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L383-396)
```go
	}
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L148-156)
```go
	userRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create user rate limiter: %w", err)
	}

	mtlsRequestRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.GatewayHTTPActionMtlsRequestRate)
	if err != nil {
		return nil, fmt.Errorf("failed to create mtls rate limiter: %w", err)
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L431-443)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
```
