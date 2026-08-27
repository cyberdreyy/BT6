### Title
Unbound cross-user response hijack via unscoped `savedCallbacks` map keyed only by attacker-controlled `MessageId` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each pending callback in `h.savedCallbacks` keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to the sender/connection that issued the request. Because the key is not sender-scoped, a second request (attacker or victim) using the same `MessageId` string silently overwrites the earlier entry, and the subsequent DON response for that `MessageId` is delivered to whoever currently occupies the map slot, not necessarily the original requester.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`:

```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [1](#0-0) 

This unconditionally overwrites any prior entry for the same `MessageId` — there is no existence check or per-sender scoping. When a DON node later responds, the lookup is likewise keyed only by `MessageId`:

```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
``` [2](#0-1) 

`pruneCallbacks` only removes entries by age/count and does nothing to prevent collisions during the (up to `CallbackMaxAgeSec`, default 120s) window an entry can remain live before being consumed or expired: [3](#0-2) 

Critically, `MessageId` is fully client-controlled: it comes straight from the incoming JSON-RPC request ID with only a length check (`len(jsonRequest.ID) > 200`), no uniqueness/collision check, and no cryptographic binding to the caller: [4](#0-3) 

By contrast, the sibling `RequestCache` abstraction used elsewhere in the same package explicitly scopes cache keys by `(sender, id)` to avoid exactly this class of collision:
```go
type globalId struct {
	sender string
	id     string
}
...
key := globalId{request.Body.Sender, request.Body.MessageId}
``` [5](#0-4) 

The capabilities handler does not use this safer pattern, instead keying `savedCallbacks` by raw `MessageId` alone (`map[string]*savedCallback`) [6](#0-5) .

Exploit flow: an attacker who can guess, predict, or otherwise learn a `MessageId` that will also be used/reused by a victim (e.g., app-level fixed/sequential IDs, or an attacker racing to reuse an ID they observed) can send their own `HandleLegacyUserMessage` request with that same `MessageId` after the victim's request is already pending but before the DON responds. This overwrites the map slot with the attacker's callback. When the DON later sends its response tagged with that `MessageId` (an echo of the original message ID, not cryptographically tied to which HTTP caller initiated it), `handleWebAPITriggerMessage` delivers the response to whichever callback currently occupies the slot — the attacker's — regardless of who actually triggered the underlying job/response.

### Impact Explanation
This is a cross-user response confusion vulnerability: an unauthenticated caller can cause another user's DON/webAPI response payload to be delivered to the attacker's own HTTP connection instead of (or in addition to overwriting) the legitimate requester's. Depending on what data flows through `MethodWebAPITrigger`/target responses, this could leak response content intended for another user. This matches the "cross-user response confusion" impact class called out in the audit scope.

### Likelihood Explanation
Exploitability requires: (1) the attacker be able to predict or reuse a `MessageId` value that a victim's request will also use, and (2) win a timing race to insert their callback into the map after the victim's insertion but before the DON's response arrives (window bounded by RPC round-trip time, up to `CallbackMaxAgeSec`=120s if the original request is slow/never resolved). No authentication or elevated privilege is needed — any unauthenticated caller of the gateway API can submit a legacy request with an arbitrary `MessageId`. Likelihood is moderate: it depends on message-ID predictability/reuse patterns from legitimate clients, which is an application-level assumption not enforced by the gateway itself. The gateway performs no server-side uniqueness or sender-binding enforcement on `MessageId`, so the underlying weakness is unconditionally present.

### Recommendation
Scope `savedCallbacks` keys by `(sender, MessageId)` similar to `requestCache`'s `globalId{sender, id}` pattern, or generate/prefix the key with a server-side unique/connection-bound token rather than trusting the raw client-supplied `MessageId` alone. Additionally, reject `HandleLegacyUserMessage` calls that attempt to reuse a `MessageId` already present in `savedCallbacks` (return an error instead of silently overwriting), mirroring `requestCache.NewRequest`'s `"request already exists"` check.

### Proof of Concept
Go handler-level test (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct two distinct `handlers.Callback` instances (`cbVictim`, `cbAttacker`).
2. Build two `api.Message`s with the same `MessageId = "shared-id"` but different `Sender` (simulating two different HTTP callers), both signed validly by different node/user keys.
3. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, cbVictim)`; assert `handler.savedCallbacks["shared-id"].Callback == cbVictim`.
4. Before any DON response arrives, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, cbAttacker)`; assert `handler.savedCallbacks["shared-id"].Callback == cbAttacker` (i.e., overwrite occurred).
5. Simulate the DON responding with `MessageId="shared-id"` via `handler.HandleNodeMessage`; assert that `cbAttacker.Wait(ctx)` receives the response while `cbVictim.Wait(ctx)` times out — demonstrating the victim's response was delivered to the attacker's callback.
6. Expected (fixed) behavior: after remediation, the second `HandleLegacyUserMessage` call should either be rejected (distinct sender collision error) or keyed separately so that `cbVictim` still receives its own response and `cbAttacker` cannot intercept it.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L53-53)
```go
	savedCallbacks  map[string]*savedCallback
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L149-152)
```go
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
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

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
	}
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

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
