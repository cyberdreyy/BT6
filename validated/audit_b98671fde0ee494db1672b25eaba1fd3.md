This is a critical finding: this exact class of collision is already recognized and fixed elsewhere in the same codebase via `RequestCache`, which keys pending requests by `globalId{sender, id}` (sender + MessageId) rather than by `MessageId` alone — explicitly to prevent cross-sender collisions. [1](#0-0) 

The `capabilities` package's `handler.HandleLegacyUserMessage`, however, keys `h.savedCallbacks` by `msg.Body.MessageId` alone, with no binding to `msg.Body.Sender`: [2](#0-1) 

And the lookup/consumption path (`handleWebAPITriggerMessage`, invoked from `HandleNodeMessage`) matches purely on `MessageId`: [3](#0-2) 

`MessageId` is fully attacker/client-chosen (up to 128 bytes, only checked for length/null-suffix in `Message.Validate`, and is part of the signed body but not derived from the signer's identity): [4](#0-3) 

So a genuine cross-user response confusion is architecturally possible in this handler if two different signers submit a `web_api_trigger` legacy message with the same `MessageId` in close succession: the second `HandleLegacyUserMessage` call overwrites `h.savedCallbacks[id]`, silently dropping the first (victim) caller's callback, and the first DON response for that `MessageId` is delivered to whichever callback is currently stored — potentially the attacker's.

That said, per the audit rules, exploitability requires the attacker to actually know/predict the victim's exact `MessageId` in advance and win a race to submit before the DON responds. The question's own premise depends on an unproven external assumption ("deterministic ID generator upstream") — nothing in this repo generates or exposes `MessageId` values to other users, and the field is chosen entirely by the calling client outside this codebase, so there's no in-repo evidence of a guessable/shared ID space. Without a concrete predictable-ID source in the repo, this doesn't meet the "reproducible, concrete" bar the rules require, though the design gap (missing sender binding on the callback map) is real and inconsistent with the more defensive `RequestCache` pattern used elsewhere.

### Title
Cross-user response confusion via unbound `MessageId` key in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores pending callbacks keyed only by client-supplied `msg.Body.MessageId`, without binding to the sender, unlike the sender-aware `RequestCache` used elsewhere in the gateway. If two distinct signers submit `web_api_trigger` requests with an identical `MessageId`, the later request silently overwrites the earlier callback entry, and the DON's subsequent response for that `MessageId` is delivered to whichever caller currently owns the map slot.

### Finding Description
`HandleLegacyUserMessage` inserts `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` with no check for an existing entry and no compound key including `msg.Body.Sender` [2](#0-1) 
`Message.Validate()` only checks length/format of `MessageId`, never uniqueness or binding to signer identity [5](#0-4) 
`handleWebAPITriggerMessage`, called from `HandleNodeMessage` when a DON node responds, looks up and deletes by `MessageId` alone and forwards the raw DON response to whatever `Callback` is registered [3](#0-2) 
By contrast, the `RequestCache` used by other gateway handlers explicitly keys by `globalId{sender, id}` to avoid exactly this class of collision, indicating the project is aware of and normally guards against sender/ID collisions [1](#0-0) 
The gap: two unrelated, differently-signed requests using the same `MessageId` string will collide in `h.savedCallbacks`, and whichever `Callback` is stored at response time wins, regardless of which request actually produced that DON response.

### Impact Explanation
If exploitable, this is a cross-user response confusion: an attacker's client receives a DON trigger response intended for another user, and the victim's request silently times out with no response ever delivered. This maps to Chainlink's "unauthorized access to another user's data/response" bounty class, but the payload exposed is limited to the trigger's `web_api_trigger` response for that DON — not credentials, keys, or fund movement directly.

### Likelihood Explanation
Exploitability is low/uncertain in practice. It requires (a) the attacker to submit a validly signed message and (b) know or predict the exact `MessageId` string that a victim's concurrent, independently-crafted request will use, and (c) win a narrow race window before the DON responds. Nothing in this repository generates or exposes `MessageId` to other users, and there's no evidence in-repo of a deterministic/shared/guessable ID scheme used by legitimate clients — that precondition lies entirely outside this codebase (client-side ID generation), so it cannot be confirmed as a repo-level vulnerability under the stated rules requiring concrete, reproducible support.

### Recommendation
Key `h.savedCallbacks` by a compound `(sender, MessageId)` identifier, mirroring `common.RequestCache`'s `globalId{sender, id}` pattern, and reject/return an error on insertion if a colliding key already exists rather than silently overwriting.

### Proof of Concept
Go handler-level test (parallel to `handler_test.go`'s existing structure):
1. Build two distinct signed `api.Message`s from two different private keys, each with `Body.MessageId = "collide-id"` and `Body.Method = MethodWebAPITrigger`.
2. Call `handler.HandleLegacyUserMessage` for victim's message with callback `cbVictim`, then immediately for attacker's message with callback `cbAttacker` (simulating a race by calling sequentially without draining in between).
3. Assert `h.savedCallbacks["collide-id"]` now points to `cbAttacker`'s wrapper (overwrite confirmed).
4. Simulate one `HandleNodeMessage` DON response carrying `MessageId = "collide-id"` (as if answering the victim's original request).
5. Assert `cbAttacker.Wait()` receives the response payload and `cbVictim.Wait()` times out/never resolves — proving cross-user response confusion, contingent on the unverified precondition that an attacker can predict the victim's `MessageId` in advance.

### Citations

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

**File:** core/services/gateway/api/message.go (L42-87)
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
