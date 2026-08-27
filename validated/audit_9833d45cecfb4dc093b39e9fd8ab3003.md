### Title
Cross-user response hijacking via colliding `MessageId` in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`HandleLegacyUserMessage` stores each user's response callback keyed solely by the attacker-controllable `msg.Body.MessageId` string, with no scoping by sender/signer identity and no existence check before writing: [1](#0-0) 

Compare this to the DON-side `RequestCache`, which keys entries by `globalId{sender, id}` (Sender + MessageId) and explicitly rejects a colliding request with `"request already exists"`: [2](#0-1) 

`MessageId` is an attacker-chosen string (up to 128 bytes, cannot end with a null byte) that is only length/format validated, not checked for global uniqueness, in `Message.Validate`: [3](#0-2) 

The signature over the message body only proves the message's own contents and the attacker's own key were used to sign it (`ExtractSigner`/`Sender` is derived from the attacker's own signature) — it in no way prevents the attacker from reusing another user's `MessageId` value in its own, independently signed request: [4](#0-3) 

Exploit flow:
1. Victim POSTs a legacy `web_api_trigger` request to the gateway with `MessageId = "M"`. `HandleLegacyUserMessage` stores `h.savedCallbacks["M"] = victimCallback` and forwards the request to all DON members.
2. Before any node responds, attacker (an unauthenticated/unprivileged gateway HTTP client who can sign its own request with its own key) POSTs a second, independently-signed `web_api_trigger` request also using `MessageId = "M"`. `HandleLegacyUserMessage` unconditionally overwrites `h.savedCallbacks["M"] = attackerCallback`, silently discarding the victim's callback reference.
3. When a DON node eventually responds to the (victim's or attacker's) `web_api_trigger` request with `MessageId = "M"`, `handleWebAPITriggerMessage` looks up and deletes `h.savedCallbacks["M"]` and delivers the payload to whichever callback is currently stored there: [5](#0-4) 

4. Because the map has no per-sender scoping and no rejection of duplicate keys, the eventual node response for the victim's request can be delivered to the attacker's callback (response leak to the wrong caller), and the victim's original callback is orphaned and eventually resolves only via a generic gateway-level request timeout (`callback.Wait` in `gateway.go`), never receiving the true response.

Nothing in `HandleLegacyUserMessage` (signature check, timestamp/staleness check, method check) validates uniqueness of `MessageId` against in-flight entries, so this path is fully reachable by an unauthenticated attacker who can predict or brute-force a victim's `MessageId` (e.g., low-entropy/sequential IDs chosen by the client, or observed IDs from earlier interactions).

### Impact Explanation
This is a cross-user request/response confusion vulnerability: an unprivileged attacker can cause a legitimate user's web API trigger response (potentially containing sensitive trigger/execution data) to be delivered to the attacker's own callback instead of the victim's, and can deny/delay the victim from ever receiving a correct response. This falls under "unauthorized access to another user's data/response" impact class for the gateway's legacy user-message handling path.

### Likelihood Explanation
Exploitability requires only unauthenticated/unprivileged access to the gateway's legacy HTTP JSON-RPC endpoint and the ability to sign a well-formed message with an attacker-controlled key (any external key works — no special permission needed) and to predict/guess the victim's `MessageId` value within the short window between the victim's request being submitted and the DON's response returning. If clients use predictable, low-entropy, or observable `MessageId`s (rather than cryptographically random UUIDs), this is straightforward and repeatable; if all clients strictly use high-entropy random IDs, practical likelihood is lower but the underlying isolation invariant is still violated by design (no defense-in-depth check), unlike the sibling `RequestCache` implementation which explicitly guards against this exact case.

### Recommendation
Scope `savedCallbacks` keys by both the signer/sender identity and `MessageId` (mirroring `RequestCache`'s `globalId{sender, id}` pattern), and reject (rather than silently overwrite) any attempt to register a `MessageId` that is already in-flight, returning an explicit conflict error to the caller as `RequestCache.NewRequest` does.

### Proof of Concept
Go table/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build two distinct `api.Message`s with `Method = MethodWebAPITrigger`, identical `MessageId = "collide-id"`, but signed by two different keys (`victimKey`, `attackerKey`), each with a distinct `handlers.Callback` (`victimCb`, `attackerCb`).
2. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`; assert `h.savedCallbacks["collide-id"].Callback == victimCb`.
3. Call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)` before any node response; assert (current buggy behavior) that `h.savedCallbacks["collide-id"].Callback` has been overwritten to `attackerCb`, and that the second call did NOT return an error to reject the collision.
4. Simulate a node responding to the victim's original request (`nodes[0]` sends a response referencing `MessageId = "collide-id"`) via `handler.HandleNodeMessage`.
5. Assert that `attackerCb.Wait(ctx)` receives the response (proving leakage to the wrong caller) while `victimCb.Wait(ctx)` never resolves except via generic timeout — demonstrating the isolation invariant is broken.
Expected fix behavior: step 3 should return an error (e.g., `"in-flight request already exists"`) and `victimCb` should remain bound to `"collide-id"`, exactly as `TestRequestCache` patterns already assert for `RequestCache.NewRequest`.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-63)
```go
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

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/api/message.go (L82-88)
```go
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```
