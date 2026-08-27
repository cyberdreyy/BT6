### Title
Cross-user response hijacking via `MessageId` collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each user's response callback in `h.savedCallbacks` keyed **only** by the attacker-controlled `msg.Body.MessageId`, with no check for an existing entry and no binding to the sender's address. Any unauthenticated client can submit a signed `web_api_trigger` request reusing another user's in-flight `MessageId`, silently overwriting the victim's callback so that the DON's response is delivered to the attacker instead of the victim.

### Finding Description
`MessageId` is a fully client-controlled field of the signed `api.Message` body [1](#0-0) . `Message.Validate()` only checks length/format and extracts the signer for `Sender`, but never ties `MessageId` uniqueness to the sender [2](#0-1) .

In `HandleLegacyUserMessage`, after validation the handler does:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [3](#0-2) 

This unconditionally overwrites any existing entry for that `MessageId`, with no `if _, exists := h.savedCallbacks[...]; exists { ... }` guard, and the map key is `msg.Body.MessageId` alone — not `(sender, MessageId)`.

When a DON node later responds, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId`, deletes it, and forwards the response to whichever callback is currently stored:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
``` [4](#0-3) 

Thus if an attacker races a second `HandleLegacyUserMessage` call with the same `MessageId` as a victim's in-flight request before the DON responds, the attacker's callback silently replaces the victim's in `savedCallbacks`. When the DON's response for that `MessageId` arrives, it is delivered to the attacker's HTTP connection, not the victim's — and the victim's original gateway request will subsequently time out with no response.

Notably, the codebase already has a hardened primitive for exactly this class of problem: `RequestCache`, used by other handlers, keys pending requests by `globalId{sender, id}` and explicitly rejects duplicate registration with `"request already exists"` [5](#0-4) . The capabilities `web_api_trigger` path does not use this cache and lacks both protections (sender-scoping and duplicate rejection).

This is reachable directly from the gateway's public HTTP entrypoint: `gateway.ProcessRequest` decodes the raw request, validates the signature, and calls `h.HandleLegacyUserMessage(ctx, msg, callback)` for any properly signed legacy request [6](#0-5) . No allowlist or per-user MessageId scoping occurs before this point (the code even has a `// TODO: apply allowlist and rate-limiting here` marker) [7](#0-6) .

### Impact Explanation
An attacker who can predict or brute-force (or simply choose the same, e.g. low-entropy or client-default) `MessageId` as a victim can cause the DON's `web_api_trigger` response — which may contain sensitive workflow trigger output — to be delivered to the attacker's connection instead of the legitimate subscriber's. This is a cross-user response/data redirection issue (isolation violation) matching the "unauthorized access to another user's data/response" bounty impact class. It does not grant fund movement or key disclosure, but it breaks the invariant that one user cannot hijack another's gateway response.

### Likelihood Explanation
Exploitability depends on the attacker knowing or guessing the victim's `MessageId` and winning the race before the DON node responds. Since `MessageId` is entirely client-chosen and not required to be cryptographically random (many client implementations may use short, sequential, or otherwise low-entropy identifiers, e.g., `"12345"` in the test fixtures) [8](#0-7) , collisions are plausible in real deployments, especially against automated/high-frequency clients with predictable ID schemes. No credentials beyond the ability to sign and submit an HTTP request to the public gateway endpoint are required — this is fully unauthenticated. The root cause (unconditional overwrite, no sender-scoping) is deterministic and trivially reproducible in a unit test.

### Recommendation
- Scope `savedCallbacks` keys by `(sender, MessageId)` similarly to `RequestCache`'s `globalId{sender, id}`, so distinct senders cannot collide.
- In `HandleLegacyUserMessage`, check for an existing entry before inserting and reject (return an error response) instead of silently overwriting, mirroring `RequestCache.NewRequest`'s `"request already exists"` behavior.
- Consider migrating the `web_api_trigger` path to use the existing `RequestCache` abstraction instead of the ad hoc `savedCallbacks` map.

### Proof of Concept
Go handler-level test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build the handler via `setupHandler(t)`.
2. Create victim message `msgA` via `triggerRequest(t, nodes[0].PrivateKey, ..., messageID="X")` signed by victim's key, and call `handler.HandleLegacyUserMessage(ctx, msgA, cbVictim)`.
3. Before any node responds, craft an attacker message `msgB` with the **same** `MessageId = "X"` (e.g., signed by a different private key / different sender), and call `handler.HandleLegacyUserMessage(ctx, msgB, cbAttacker)`.
4. Assert that the second call either (a) fails with an explicit "duplicate MessageId" error (desired fixed behavior), or (b) currently succeeds and overwrites `h.savedCallbacks["X"]` (demonstrating the bug).
5. Simulate the DON response for `MessageId "X"` via `handler.HandleNodeMessage(...)`.
6. Assert that in the current (buggy) implementation, `cbAttacker.Wait(ctx)` receives the response while `cbVictim.Wait(ctx)` times out/never resolves — proving cross-user response hijacking. After the fix, the second `HandleLegacyUserMessage` call should be rejected up front and `cbVictim` should correctly receive the DON response.

### Citations

**File:** core/services/gateway/api/message.go (L42-52)
```go
type MessageBody struct {
	MessageId string `json:"message_id"`
	Method    string `json:"method"`
	DonId     string `json:"don_id"`
	Receiver  string `json:"receiver"`
	// Service-specific payload, decoded inside the Handler.
	Payload json.RawMessage `json:"payload,omitempty"`

	// Fields only used locally for convenience. Not serialized.
	Sender string `json:"-"`
}
```

**File:** core/services/gateway/api/message.go (L54-88)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
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

**File:** core/services/gateway/gateway.go (L264-273)
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
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L193-200)
```go
func triggerRequest(t *testing.T, key *ecdsa.PrivateKey, topics []string, methodName, timestamp, payload string) *api.Message {
	messageID := "12345"
	if methodName == "" {
		methodName = MethodWebAPITrigger
	}
	if timestamp == "" {
		timestamp = strconv.FormatInt(time.Now().Unix(), 10)
	}
```
