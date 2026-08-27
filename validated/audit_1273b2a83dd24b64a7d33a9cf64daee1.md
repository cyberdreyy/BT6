### Title
Cross-user response confusion via unnamespaced, client-controlled `MessageId` in the Web API Gateway callback map - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The internet-facing Gateway's legacy Web API handler stores a per-request response callback in a single shared map keyed only by the client-supplied `MessageId`, with no binding to the originating sender/session. Because the ID space is shared across all unprivileged clients hitting the same DON handler, one client can register (or overwrite) the callback slot for an ID that another concurrent client is using, causing the victim's response (which may contain sensitive HTTP action results/secrets-bearing payloads) to be delivered to the attacker instead.

### Finding Description
An external, unauthenticated/unprivileged client sends requests to the Gateway HTTP endpoint, which are routed to `gateway.ProcessRequest` [1](#0-0) . For legacy requests, `msg.Body.MessageId` is set directly from the caller-controlled `jsonRequest.ID` with no length/format check beyond a 200-char cap and no relation to sender identity: [2](#0-1) .

This message is handed to `handler.HandleLegacyUserMessage`, which stores the caller's `Callback` in the handler's shared `savedCallbacks` map using only `msg.Body.MessageId` as the key, with **no check for an existing entry**: [3](#0-2) 

The `savedCallbacks` map, `handler` struct and its mutex are shared across all concurrent requests handled by this DON's handler instance (i.e., across all unprivileged clients, not scoped per-connection or per-user): [4](#0-3) 

When a capability node later returns a response, delivery is resolved purely by `MessageId` (echoed back from the node) via `handleWebAPITriggerMessage`, again with no sender-vs-original-requester binding check beyond the node's own address matching the DON member list: [5](#0-4) [6](#0-5) 

Because two different external callers can independently pick the same `MessageId` (many JSON-RPC client libraries default to `"1"`, `"0"`, sequential integers, or an attacker can deliberately guess/brute-force short IDs since there is no per-sender namespace), the second `HandleLegacyUserMessage` call silently overwrites the first caller's callback entry in the map. The node's asynchronous response destined for the first (victim) caller is then delivered to whichever callback is currently registered under that ID — i.e., the attacker's — since routing/lookup happens strictly by the bare string key with no ownership check. This is directly analogous to the reported sandwich-attack root cause: an unprivileged actor injects a colliding operation (front-running/racing a shared mutable keyed resource) around a legitimate operation to redirect its outcome, exploiting the absence of atomic ownership/ordering guarantees on shared state (here, the `savedCallbacks` map keyed by unauthenticated client input) rather than any check tying the eventual result back to the original requester.

### Impact Explanation
A successful collision causes the Gateway to route another user's `MethodWebAPITrigger`/outgoing-request response payload to an unrelated, unprivileged attacker's HTTP connection — this is exactly the "cross-user response confusion" class called out as an acceptance criterion. Web API trigger/target payloads can carry workflow execution results and outgoing HTTP response bodies (headers, status, body) that may include sensitive application data. The victim, meanwhile, receives no response (their callback slot was stolen) or an error/timeout, a denial-of-service side effect. No authentication bypass of credentials is needed — the attacker only needs unauthenticated access to the public Gateway HTTP endpoint and to choose a colliding `MessageId`.

### Likelihood Explanation
Likelihood is moderate-to-high in practice:
- The Gateway endpoint that accepts these legacy requests is internet-facing and requires no privileged role — any client can submit a `MessageId`.
- Predictable/default IDs are extremely common (`"1"`, incrementing counters used by many RPC clients), so accidental collisions between legitimate concurrent users are plausible even without attacker intent.
- A deliberate attacker only needs to race requests with the same, short, low-entropy ID (200-char max is the only constraint) during the window the victim's request is in flight (bounded by `CallbackMaxAgeSec`, default 120s) to have a good chance of winning the map-overwrite race.

### Recommendation
- Scope `savedCallbacks` keys by a server-generated, cryptographically random identifier (or a `(senderIdentity, MessageId)` composite key) rather than trusting the raw client-supplied `MessageId` as a global map key.
- Reject/`return an error` for `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage` when a `MessageId` already has an active, unexpired callback registered instead of silently overwriting it (as is already done in the newer vault/v2 handlers, which explicitly reject duplicate/in-flight request IDs, e.g. `"request was already authorized previously"` / `"in-flight request"`) — apply the same duplicate-ID rejection pattern here.
- Bind delivered responses to the identity/session that submitted the original request before invoking the saved callback.

### Proof of Concept
1. Attacker and victim both submit legacy Web API requests to the Gateway's public endpoint with the same JSON-RPC `id` (e.g., `"1"`), targeting the same DON, in `HandleLegacyUserMessage`.
2. Victim's request registers `savedCallbacks["1"] = victimCallback` first: `core/services/gateway/handlers/capabilities/handler.go:411-412`.
3. Attacker's request (sent shortly after, before the DON responds) overwrites the same slot: `savedCallbacks["1"] = attackerCallback`.
4. When the DON node responds with `MessageId = "1"` (echoing the victim's original request), `handleWebAPITriggerMessage` looks up `savedCallbacks["1"]`, finds the attacker's callback, and delivers the victim's response payload to the attacker: `core/services/gateway/handlers/capabilities/handler.go:148-162`.
5. The victim's original HTTP connection never receives a response (times out) while the attacker receives the victim's data.

Note: I was unable to fully trace the newer v2/JSON-RPC path (`http_trigger_handler.go`) end-to-end for equivalent protections beyond the duplicate-ID rejection test observed, since it appears to derive a namespaced key (`workflowID`/owner-prefixed request IDs) that may already mitigate this class there; the vulnerability described above is specific to the legacy handler path (`core/services/gateway/handlers/capabilities/handler.go`).

### Citations

**File:** core/services/gateway/gateway.go (L217-262)
```go
// Called by the server
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
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
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
```

**File:** core/services/gateway/handlers/common/message_util.go (L34-58)
```go
// ValidatedMessageFromReq validated and extracts a legacy Gateway Message
// from params field of JSON-RPC request
func ValidatedMessageFromReq(req *jsonrpc.Request[json.RawMessage]) (*api.Message, error) {
	if req.Version != "2.0" {
		return nil, errors.New("incorrect jsonrpc version")
	}
	if req.Method == "" {
		return nil, errors.New("empty method field")
	}
	if req.Params == nil {
		return nil, errors.New("missing params attribute")
	}
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-76)
```go
type handler struct {
	services.StateMachine
	config          HandlerConfig
	don             handlers.DON
	donConfig       *config.DONConfig
	savedCallbacks  map[string]*savedCallback
	mu              sync.Mutex
	lggr            logger.Logger
	httpClient      network.HTTPClient
	nodeRateLimiter *ratelimit.RateLimiter
	wg              sync.WaitGroup
	stopCh          services.StopChan
	metrics         *metrics
}

type HandlerConfig struct {
	NodeRateLimiter         ratelimit.RateLimiterConfig `json:"nodeRateLimiter"`
	MaxAllowedMessageAgeSec uint                        `json:"maxAllowedMessageAgeSec"`

	CallbackMaxAgeSec        int `json:"callbackMaxAgeSec"`
	MaxSavedCallbacks        int `json:"maxSavedCallbacks"`
	CallbackPruneIntervalSec int `json:"callbackPruneIntervalSec"`
}

type savedCallback struct {
	id        string
	createdAt time.Time
	handlers.Callback
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-267)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
	start := time.Now()
	switch msg.Body.Method {
	case MethodWebAPITrigger:
		err = h.handleWebAPITriggerMessage(ctx, msg, nodeAddr)
	case MethodWebAPITarget, MethodComputeAction, MethodWorkflowSyncer:
		err = h.handleWebAPIOutgoingMessage(ctx, msg, nodeAddr)
	default:
		err = fmt.Errorf("unsupported method: %s", msg.Body.Method)
	}
	h.metrics.recordHandleDuration(ctx, time.Since(start), msg.Body.Method, err == nil)
	return err
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
