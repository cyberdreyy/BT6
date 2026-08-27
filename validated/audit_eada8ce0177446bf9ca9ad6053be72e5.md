Confirmed: `msg.Body.MessageId = request.ID` at [1](#0-0) , where `request.ID` is the raw JSON-RPC `id` field of the incoming user request — fully client-supplied, with only a length cap (`len(jsonRequest.ID) > 200`) enforced in `gateway.go` [2](#0-1) . There is no uniqueness enforcement, no per-sender namespacing, and no binding of `MessageId` to the requester's identity anywhere before it's used as the sole map key in `savedCallbacks`.

### Title
Cross-user response injection via attacker-controlled MessageId collision in savedCallbacks map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores each user's response callback in `h.savedCallbacks` keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to the sender's identity. Because `MessageId` is taken verbatim from the untrusted JSON-RPC request `id` field, a second (attacker) request using the same `MessageId` as an in-flight legitimate request silently overwrites the first user's callback entry, so the node response ultimately intended for the original message id is delivered to whichever caller registered last.

### Finding Description
The gateway decodes the incoming JSON-RPC request and directly copies its client-controlled `id` field into `msg.Body.MessageId`: [3](#0-2) . This message flows into `gateway.(*gateway).ProcessRequest`, which validates format only (signature, length caps, DonId) but never checks `MessageId` uniqueness or ties it to the sender: [4](#0-3) .

In `handler.HandleLegacyUserMessage`, the callback is registered purely by this attacker-controlled string: `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` [5](#0-4) . If two different callers submit requests with the same `MessageId` while the first is still outstanding, the second overwrites the first entry — the map has no notion of "already claimed by user A."

When a DON node later replies for that message id, `HandleNodeMessage` only verifies the responding node's own signature/address (`msg.Body.Sender != nodeAddr`) [6](#0-5) , then `handleWebAPITriggerMessage` looks the response up strictly by `MessageId` and delivers it to whatever callback currently occupies that slot, deleting the entry after use: [7](#0-6) . Nothing re-checks that the callback belongs to the same HTTP caller who originally submitted that request. If the attacker's overwrite happens after user A's request but before the DON has replied, the node's genuine reply for user A's trigger request is delivered into the attacker's `Callback.SendResponse`, and the attacker instead receives A's data (or, depending on timing, A receives content correlated with the attacker's registration/timestamp confusion). Since typical JSON-RPC clients often use small sequential or predictable ids ("1", "2", ...), this collision is easily producible by an unprivileged caller without needing DON compromise — only knowledge/guessing of the target's `id` and the ability to race the request before the DON responds.

### Impact Explanation
This breaks the isolation invariant between concurrent gateway users: a message intended for one caller's callback channel can be captured by a different, unrelated caller by supplying a colliding `MessageId`. This matches the "cross-user response confusion" bounty class — response payloads (potentially containing external HTTP payload data, trigger results) can leak from one caller session to another purely via HTTP-layer request submission, with no operator or DON-side compromise required.

### Likelihood Explanation
The only precondition is that the attacker can predict or match another caller's `MessageId` value (e.g. small sequential integers used as JSON-RPC ids by common client libraries, or ids observed from logs/error messages/timing) and race its own `HandleLegacyUserMessage` registration against the still-open window before the DON node responds (bounded by `CallbackMaxAgeSec`, default 120s [8](#0-7) ). No credentials beyond ordinary gateway API access are required, and the attack is repeatable per request.

### Recommendation
Derive the `savedCallbacks` key from a value that is unique per registration and cannot be freely chosen/collided by a different caller — e.g., namespace the map key by combining `MessageId` with a server-generated nonce/requester fingerprint, or reject registration outright if an unexpired entry already exists for that `MessageId` (`HandleLegacyUserMessage` should check `if _, exists := h.savedCallbacks[msg.Body.MessageId]; exists { return error }` before storing) instead of silently overwriting.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Create two distinct `handlers.Callback` instances (`cbA`, `cbB`) representing two different logical users.
2. Build two `api.Message` values via the existing `triggerRequest` helper but force both to share the same `MessageId` (e.g., "12345") and different payload/topics distinguishing A vs B.
3. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` then `handler.HandleLegacyUserMessage(ctx, msgB, cbB)` — assert the second call either returns an error ("duplicate message id") in a fixed implementation, or (to demonstrate the bug in current code) assert that `h.savedCallbacks["12345"].Callback == cbB` after both calls, proving `cbA` was silently evicted.
4. Simulate the DON node reply for the *original* message A (`hc.ValidatedResponseFromMessage(msgA)`), call `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
5. Assert (in current vulnerable code) that `cbB.Wait(ctx)` receives the response — i.e., user B's callback resolves with data related to user A's request — while `cbA.Wait(ctx)` times out/never resolves, proving cross-user delivery.

### Citations

**File:** core/services/gateway/api/jsonrpccodec.go (L24-33)
```go
func (*JsonRPCCodec) DecodeJSONRequest(request jsonrpc2.Request[json.RawMessage]) (*Message, error) {
	var msg Message
	err := json.Unmarshal(*request.Params, &msg)
	if err != nil {
		return nil, err
	}
	msg.Body.MessageId = request.ID
	msg.Body.Method = request.Method
	return &msg, nil
}
```

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
