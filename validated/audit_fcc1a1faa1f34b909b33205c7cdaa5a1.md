### Title
Client-controlled JSON-RPC request ID used as unchecked map key allows callback overwrite / cross-user response hijacking in the Web API Gateway legacy trigger handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The external report's core complaint is that using an unvalidated, externally-influenced value as the primary key/cardinality for a data structure — with no duplicate/uniqueness check — allows one submission to silently overwrite another, causing data loss and cross-request confusion. The same anti-pattern exists in the Chainlink Gateway's legacy Web API trigger handler: the pending-callback map is keyed directly by the client-supplied JSON-RPC request ID (`msg.Body.MessageId`), with no check for an existing entry before writing, unlike the equivalent (correct) implementations elsewhere in the same gateway package.

### Finding Description
When an unprivileged client sends a legacy gateway request, `ValidatedMessageFromReq` copies the caller-supplied JSON-RPC `req.ID` directly into `msg.Body.MessageId`: [1](#0-0) 

`HandleLegacyUserMessage` (production handler for `MethodWebAPITrigger` messages) then stores the caller's callback in a shared map keyed solely by this attacker-controlled `MessageId`, without checking whether an entry already exists for that key: [2](#0-1) 

Later, when a DON node responds, the response is matched purely by `msg.Body.MessageId`, the entry is popped and its callback invoked: [3](#0-2) 

Because `MessageId` is entirely attacker-chosen and there is no per-sender namespacing (unlike `globalId{sender, id}` used in `RequestCache`) and no "already exists" rejection (unlike `RequestCache.NewRequest`, which explicitly does `if ok { return errors.New("request already exists") }`), a second unprivileged client can submit a request using the same `MessageId` as an in-flight request from a different client. This overwrites `h.savedCallbacks[msg.Body.MessageId]`, orphaning the first caller's callback and causing whichever response arrives for that ID to be delivered to the second (attacker's) callback instead. [4](#0-3) 

This is exactly the class of bug described in the report: an externally-influenced identifier used as sole cardinality/key for a shared data structure, with no uniqueness validation, leading to overwrite and cross-request ambiguity — versus the `RequestCache`/vault handler code paths in the same codebase that correctly reject duplicate IDs (`"request already exists"`, `"request was already authorized previously"`). [5](#0-4) 

The identical unguarded pattern also exists in `handler.dummy.go`: [6](#0-5) 

### Impact Explanation
An unprivileged client can hijack or disrupt another unprivileged client's pending gateway request/response cycle by choosing a colliding `MessageId`:
- The victim's request is silently dropped from `savedCallbacks` (their HTTP call will simply hang until any upstream timeout, receiving no response, or an unrelated response).
- Depending on timing, the response intended for the victim's request may instead be delivered to the attacker's callback, since matching happens only on `MessageId` with no sender binding.
This is a request-impersonation / cross-user response confusion vector reachable directly from unauthenticated HTTP clients of the gateway's user-facing port.

### Likelihood Explanation
`MessageId` is fully attacker-controlled (it is simply the JSON-RPC `id` field of the request, validated only for length ≤200 characters at `core/services/gateway/gateway.go`). No authentication or session binding constrains its value, and there is no server-side uniqueness or expiry check gating the write into `savedCallbacks`, so triggering a collision requires only that an attacker send a request with a `MessageId` that coincides with (or is guessed/observed for) another pending in-flight request. In busy or predictable-ID scenarios this is a realistic attack; the window is bounded by `CallbackMaxAgeSec` (default 120s) during which the ID stays "live" in the map.

### Recommendation
- Key `savedCallbacks` by a composite of sender identity and `MessageId` (as `RequestCache` already does with `globalId{sender, id}`), not by `MessageId` alone.
- Reject (rather than silently overwrite) writes when an entry for the same key already exists, mirroring the `"request already exists"` check in `core/services/gateway/handlers/common/requestcache.go` and the vault handler's duplicate-request rejection.
- Consider migrating `capabilities/handler.go`'s legacy trigger flow to use the existing `RequestCache` abstraction instead of maintaining its own separate `savedCallbacks` map, eliminating the duplicated, inconsistent cardinality/validation logic — directly addressing the report's recommendation to consolidate data structures and centralize invariant checks in one code path.

### Proof of Concept
1. Client A sends a legacy JSON-RPC request to the gateway's user-facing HTTP endpoint with `id = "X"` and `method = web_api_trigger`; `HandleLegacyUserMessage` stores `savedCallbacks["X"] = callbackA` and forwards the request to all DON members.
2. Before any node responds, Client B (unprivileged, unrelated) sends its own legacy request also with `id = "X"`; this again calls `HandleLegacyUserMessage`, which overwrites `savedCallbacks["X"] = callbackB` with no existence check.
3. When a DON node responds for `MessageId = "X"` (which could be the response to either A's or B's forwarded payload, since both were sent under the same ID), `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds `callbackB`, deletes the entry, and delivers the response to Client B.
4. Client A's original callback (`callbackA`) is never invoked; its HTTP connection stalls with no response, while Client B may receive a response not generated from its own request.

Note: I was not able to fully trace whether the gateway's user-facing HTTP endpoint enforces any per-request authentication before reaching `HandleLegacyUserMessage` (the `auth` parameter passed into `jsonrpc2.DecodeRequest` in `gateway.go` was not fully inspected end-to-end); if such method-level authentication exists for `web_api_trigger`, it would still not prevent two distinct *authenticated but mutually untrusted* callers from colliding on `MessageId`, since the flaw is independent of caller identity verification. If further confirmation of the exact authentication requirements on this endpoint is needed, a full Devin session with codebase access would be needed to trace `gw_net.HTTPServer`'s request-to-`ProcessRequest` wiring in detail.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/requestcache.go (L34-63)
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
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L682-725)
```go
	t.Run("unhappy path - duplicate requestId", func(t *testing.T) {
		h, callback, don, _ := setupHandler(t)
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

		requestID := "1"
		reqData := &vaultcommon.ListSecretIdentifiersRequest{
			RequestId: requestID,
			Owner:     owner,
		}
		reqDataBytes, err := json.Marshal(reqData)
		require.NoError(t, err)

		validJSONRequest := jsonrpc.Request[json.RawMessage]{
			ID:     requestID,
			Method: vaulttypes.MethodSecretsList,
			Params: (*json.RawMessage)(&reqDataBytes),
		}

		responseData := &vaultcommon.ListSecretIdentifiersResponse{
			Identifiers: []*vaultcommon.SecretIdentifier{
				{
					Key:       "foo",
					Owner:     owner,
					Namespace: "default",
				},
			},
		}
		resultBytes, err := json.Marshal(responseData)
		require.NoError(t, err)
		expectedRequestID := owner + vaulttypes.RequestIDSeparator + requestID
		response := jsonrpc.Response[json.RawMessage]{
			ID:     expectedRequestID,
			Result: (*json.RawMessage)(&resultBytes),
			Method: vaulttypes.MethodSecretsList,
		}
		resultBytes, err = json.Marshal(responseData)
		require.NoError(t, err)

		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.NoError(t, err)

		// send duplicate request
		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.ErrorContains(t, err, "request was already authorized previously")
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-66)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
```
