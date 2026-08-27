### Title
Cross-user response confusion via unbound `savedCallbacks` map keyed only by `MessageId` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.savedCallbacks` is keyed solely by `msg.Body.MessageId` (a value fully controlled by the requesting client, propagated from the JSON-RPC `ID` field via `ValidatedMessageFromReq`), with no binding to the originating request's sender/signer identity. `handleWebAPITriggerMessage` looks up and deletes entries purely by `MessageId`, so a second unprivileged caller who submits a `web_api_trigger` request reusing a `MessageId`, and whose entry overwrites or coincides in time with another user's pending entry, can have their callback satisfied with response data intended for a different user's request.

### Finding Description
`HandleLegacyUserMessage` unconditionally writes into the map without checking for collisions: `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` [1](#0-0) . This overwrites any pre-existing entry for the same ID silently. On the node-response path, `handleWebAPITriggerMessage` retrieves and deletes the callback purely by `MessageId` and sends whichever node response arrives first to whatever callback is currently registered under that ID: [2](#0-1) .

The `MessageId` is attacker-controlled: it comes from the JSON-RPC request `ID` field set by the calling client (`m.Body.MessageId = req.ID`) [3](#0-2) , and the only validation is a length/format check with no uniqueness or sender-binding requirement [4](#0-3) . `HandleNodeMessage` verifies the response sender matches the expected `nodeAddr` (protecting node authenticity) but does nothing to correlate the accepted response back to the specific requesting client that originated the pending callback: [5](#0-4) .

This is a real gap in this legacy handler: a newer/parallel implementation in the same codebase, `common.requestCache`, explicitly binds cache entries to `globalId{sender, id}` rather than `id` alone, demonstrating that sender-binding is the recognized correct pattern elsewhere in the gateway handlers package: [6](#0-5) . `capabilities/handler.go`'s `savedCallbacks` does not follow this pattern.

Exploit flow: User A submits a `web_api_trigger` with `MessageId=M`; the gateway broadcasts A's request to all DON members [7](#0-6) . If A's entry is removed (either by first node response, delivered normally, or pruned/expired) and User B then submits a request with the same `MessageId=M` before a straggling/delayed response to A's original broadcast arrives, that delayed response — still carrying `MessageId=M` — will be delivered to B's callback via `handleWebAPITriggerMessage`, since the lookup has no way to distinguish "A's request" from "B's request" beyond the bare string ID.

### Impact Explanation
This causes cross-user response confusion: User B receives response data/content that was generated for User A's original web API trigger request, potentially exposing data not intended for B and violating the isolation invariant between two unprivileged callers' pending requests. This matches Chainlink's bounty impact class of unauthorized disclosure of another user's data / request-response confusion, though it is capped by feasibility constraints below.

### Likelihood Explanation
Exploitability requires: (1) knowledge/prediction of another user's `MessageId` (not by default secret, but also not guaranteed to be discoverable — depends on how upstream API/GraphQL layers generate the ID before it reaches the gateway; this repository's index does not show what generates `MessageId` values for real end-user requests, only that the field is technically attacker-settable at the JSON-RPC transport level), (2) a timing race where a stale/delayed node response for the first user's request arrives after the second user's identically-ID'd request is registered and before the second request's own response arrives. Both nodes must have received A's broadcast (they do, since it's sent to all DON members) and one such node must respond late relative to B's registration. This is a race-dependent, non-trivial-but-plausible condition rather than a guaranteed one-shot exploit, and depends on whether the caller of `HandleLegacyUserMessage` (outside this file) allows arbitrary/attacker-chosen IDs to collide across distinct users — this upstream detail could not be fully confirmed within the indexed code.

### Recommendation
Bind `savedCallbacks` keys to both the request `MessageId` and the identity of the originating caller (e.g., signer/sender address or a per-connection session identifier), mirroring the `globalId{sender, id}` pattern already used in `common.requestCache`. Additionally, reject (rather than silently overwrite) new registrations that collide with an existing unexpired entry for the same key.

### Proof of Concept
Go handler-level test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler `h` with a mocked `handlers.DON` that no-ops `SendToNode`.
2. Register callback A: call `h.HandleLegacyUserMessage(ctx, msgA, callbackA)` with `msgA.Body.MessageId = "M"`, `msgA.Body.Sender = "userA"`. Confirm `h.savedCallbacks["M"]` exists and is bound to `callbackA`.
3. Simulate A's completion via a node response (`handleWebAPITriggerMessage`) that deletes `"M"` and calls `callbackA.SendResponse`.
4. Register callback B: call `h.HandleLegacyUserMessage(ctx, msgB, callbackB)` with the same `MessageId = "M"`, `msgB.Body.Sender = "userB"`.
5. Simulate a second, delayed node response for the original `msgA` broadcast (still carrying `MessageId = "M"`, from a different DON member) by invoking `h.handleWebAPITriggerMessage(ctx, delayedRespForA, nodeAddr2)`.
6. Assert that `callbackB.SendResponse` is invoked with data derived from `delayedRespForA` (A's response payload) — proving cross-user delivery — instead of asserting an error/no-op.
7. Expected (fixed) behavior: the delayed response for A should not match B's callback because the key would include `sender`/session binding; assert `callbackB.SendResponse` is never called with A's payload, and/or that stale keys tied to a different original sender are rejected.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-420)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/handlers/common/message_util.go (L46-57)
```go
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
```

**File:** core/services/gateway/api/message.go (L54-87)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
```

**File:** core/services/gateway/handlers/common/requestcache.go (L34-76)
```go
type globalId struct {
	sender string
	id     string
}

type pendingRequest[T any] struct {
	handlers.Callback
	responseData *T
	timeoutTimer *time.Timer
	mu           sync.Mutex
}

func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```
