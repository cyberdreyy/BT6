### Title
Unprivileged request-ID collision in the confidential relay gateway handler causes legitimate user requests to be force-rejected - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
The gateway's confidential relay handler tracks in-flight requests in a single process-wide map keyed only by the client-supplied JSON-RPC `id` field, with no per-sender/owner namespacing. Any unprivileged caller reaching the gateway's public HTTP endpoint can pick an arbitrary `id` and, if another (legitimate) caller's in-flight request happens to use the same `id`, the second submission is unconditionally rejected — exactly analogous to the reported `nonETHReuse` front-running issue, where a shared, unscoped lock/state variable lets an unrelated caller cause a legitimate caller's transaction/request to revert.

### Finding Description
The public gateway entrypoint `gateway.ProcessRequest` decodes an arbitrary, attacker-supplied JSON-RPC request straight off the HTTP body and routes it to the target handler using the client-supplied `id` with only a length check (≤200 chars) — no ownership binding is performed at this layer: [1](#0-0) [2](#0-1) 

For the confidential relay handler (`MethodSecretsGet` / `MethodCapabilityExec`), `HandleJSONRPCUserMessage` only validates that `req.ID` is non-empty and not too long before calling `newActiveRequest`: [3](#0-2) 

`newActiveRequest` stores the request in a single shared `map[string]*activeRequest` keyed purely by `req.ID` — there is no per-sender, per-owner, or per-workflow scoping of this key, and no authentication/authorization check is performed before the entry is created or before the collision check runs: [4](#0-3) 

If an entry with the same `req.ID` already exists in `activeRequests`, the call is hard-rejected with `"request ID already exists"`: [5](#0-4) 

This mirrors the reported bug class precisely: a piece of shared, globally-scoped state (there: `_status`; here: the `activeRequests[req.ID]` slot) is written by the first caller to reach it and blocks any subsequent legitimate caller from proceeding until the state is cleared — here, cleared only when the original request completes, is bundled, or expires after `RequestTimeoutSec` (default 30s): [6](#0-5) [7](#0-6) 

Unlike the contract case, there is no `Multicall`-style unlock available to the victim; the victim's request is simply dropped with an error and the caller must retry with a different `id`, and can be blocked again by a repeat of the same technique.

### Impact Explanation
An unprivileged network caller who can predict, observe, or brute-force another caller's chosen `id` value (or who simply floods the gateway with a wide set of `id`s to maximize collision probability against normal traffic) can cause targeted requests for `secrets.get`/capability-exec relay calls to be rejected outright for up to the request timeout window. This is a denial-of-service against a specific request rather than fund loss, but it directly matches the report's "Medium impact — request gets reverted" characterization, since the victim's genuine gateway call fails even though nothing was wrong with it.

### Likelihood Explanation
Exploitability depends on the attacker's ability to guess or observe the exact `id` in use before the collision window closes. The gateway does not document or enforce that `id` values must be unpredictable/random per caller, and there is no scoping by sender/DON/workflow to prevent cross-user collisions once an `id` is guessed or observed (e.g., replayed from network traffic, logs, or shared client libraries that use deterministic/sequential IDs). This is the same "medium likelihood, requires an unusual direct interaction" profile as the original report — it is not the primary way honest clients are expected to use the API (well-behaved clients use random UUIDs), but nothing in the code prevents or even flags the collision as anomalous/malicious versus routine race conditions from retries.

### Recommendation
Scope `activeRequests` keys by both `req.ID` and an authenticated caller identity (e.g., DON member ID, mTLS/JWT-derived sender, or workflow owner) rather than by client-supplied `id` alone, so that one caller cannot collide with another caller's in-flight request. Additionally, consider generating/validating request IDs server-side (or requiring cryptographically random IDs) and returning a distinct, non-ambiguous error/retry signal instead of a hard failure when a collision is detected, so a legitimate caller is not silently starved for up to `RequestTimeoutSec`.

### Proof of Concept
1. Attacker crafts a JSON-RPC request to the gateway's public HTTP endpoint with `method: "capability.exec"` (or `secrets.get`) and a chosen `id = "X"`, then submits it via `gateway.ProcessRequest` → `handler.HandleJSONRPCUserMessage` → `newActiveRequest`, which inserts `activeRequests["X"]`.
2. Before the attacker's request completes/expires (up to `RequestTimeoutSec`, default 30s), the legitimate victim submits a genuine request that also uses `id = "X"` (e.g., because IDs are predictable/sequential in the victim's client, or the attacker observed/replayed the ID).
3. `newActiveRequest` finds `h.activeRequests["X"] != nil` and returns `"request ID already exists: X"`, which propagates back to `gateway.ProcessRequest` as an `api.HandlerError`, causing the victim's legitimate request to fail with no on-chain/off-chain action taken. [8](#0-7) [9](#0-8)

### Citations

**File:** core/services/gateway/gateway.go (L218-231)
```go
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/gateway.go (L264-276)
```go
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
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L36-36)
```go
	defaultRequestTimeoutSec  = 30
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L659-679)
```go
func (h *handler) sendResponseAndClearRequest(ctx context.Context, ar *activeRequest, payload gwhandlers.UserCallbackPayload) error {
	if !ar.completed.CompareAndSwap(false, true) {
		// Another path already answered this request.
		return nil
	}

	sendErr := ar.SendResponse(payload)

	h.mu.Lock()
	delete(h.activeRequests, ar.req.ID)
	h.mu.Unlock()

	if sendErr != nil {
		h.lggr.Errorw("error sending response to user", "requestID", ar.req.ID, "error", sendErr)
		return sendErr
	}

	h.recordMetrics(ctx, payload.ErrorCode)
	h.lggr.Debugw("response sent to user", "requestID", ar.req.ID, "errorCode", payload.ErrorCode)
	return nil
}
```
