Confirmed: `Message.Validate()` only checks the signature is well-formed and recovers the signer address into `m.Body.Sender` — it does not check that the sender is an authorized DON member, workflow owner, or otherwise privileged entity. Any actor holding an arbitrary ECDSA keypair can self-sign a `MethodWebAPITrigger` message and reach `handler.HandleLegacyUserMessage` through `gateway.ProcessRequest` → `multiHandler.HandleLegacyUserMessage`, with no allowlist/rate-limit check on this path (explicitly marked `// TODO: apply allowlist and rate-limiting here`).

### Title
Unauthenticated flooding of `web_api_trigger` requests causes deterministic, targeted eviction of a victim's pending gateway callback (DoS) - (core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` stores every accepted `MethodWebAPITrigger` request in `h.savedCallbacks` keyed by the attacker-controllable `MessageId`, with no rate limiting or allowlist check on this path. `pruneCallbacks` evicts the oldest entries by `createdAt` once the map exceeds `MaxSavedCallbacks` (default 20000), so an attacker who floods enough uniquely-IDed requests within one `CallbackPruneIntervalSec` window (default 30s) can deterministically force eviction of any earlier-submitted victim callback, dropping that user's pending gateway response.

### Finding Description
- `gateway.ProcessRequest` (core/services/gateway/gateway.go:218-291) only validates the message signature format (`msg.Validate()`) before dispatching to the handler; it performs no per-sender authorization or throttling. [1](#0-0) 
- `Message.Validate()` (core/services/gateway/api/message.go:54-88) merely checks field lengths and recovers `Body.Sender` from the signature — any keypair works, there is no allowlist check of the signer against DON members or job owners. [2](#0-1) 
- `handler.HandleLegacyUserMessage` explicitly notes rate-limiting/allowlisting is not yet implemented on this path, then unconditionally inserts the callback into the shared map keyed by attacker-supplied `msg.Body.MessageId`: [3](#0-2) 
- `pruneCallbacks` sorts all callbacks by `createdAt` and deletes the oldest half whenever the map exceeds `MaxSavedCallbacks`, purely based on insertion order — with no notion of sender identity or fairness: [4](#0-3) 

Exploit flow: attacker observes (or can reasonably infer, e.g. via known trigger cadence) that a victim's request was just accepted into `savedCallbacks`. Attacker then floods ≥`MaxSavedCallbacks` uniquely-`MessageId` signed `web_api_trigger` requests (self-signed, no privileged credentials needed) before the next prune tick. When `pruneCallbacks` runs, it sorts by `createdAt` and evicts the oldest `len-maxSize/2` entries — the victim's older entry is deterministically evicted before the flood of newer attacker entries, since eviction order depends solely on timestamp, not identity. The victim's node-side response (delivered later via `HandleNodeMessage`/`handleWebAPITriggerMessage`) then finds no matching entry (`found == false` at handler.go:150-161) and is silently dropped — the victim's HTTP client hangs until gateway-side timeout / `RequestTimeoutError`.

### Impact Explanation
This is a **denial-of-service against a specific, unauthenticated victim's asynchronous request**, not a random/incidental capacity overflow: attacker-driven flooding lets them deterministically choose which pending user request is dropped based on submission timing, causing that request to silently time out rather than receive its DON-computed response. Impact class: targeted Denial of Service via resource-exhaustion of a shared unauthenticated map, resulting in silent request/response loss for another user.

### Likelihood Explanation
- No credentials beyond the ability to generate an ECDSA keypair and send HTTP/gateway JSON-RPC requests are needed — fully unprivileged/unauthenticated.
- No rate limiting or allowlist gates this specific ingress path (explicitly a known TODO in the code).
- Feasibility requires sending `MaxSavedCallbacks` (20000, or `maxSavedCallbacks/2` for a lighter flood since eviction removes the oldest half) valid signed messages inside one `CallbackPruneIntervalSec` (30s default), which is realistic for a scripted attacker generating unique signatures/message IDs.
- The main practical requirement is being able to correlate the flood timing to just after a victim's request lands (ordering-only knowledge, not victim's `MessageId` or private data) — reasonably achievable for a victim whose request cadence is externally observable (e.g., predictable webhook/trigger polling, or an attacker triggering the victim's action then immediately flooding).
- Repeatable indefinitely; each 30s window offers another attack opportunity.

### Recommendation
- Implement the pending TODO: apply per-sender allowlisting and rate-limiting to `HandleLegacyUserMessage` before inserting into `savedCallbacks`, so an unauthorized/unlimited sender cannot occupy the map.
- Bound the map growth per-sender (e.g., cap callbacks per `Body.Sender`) instead of relying purely on a global oldest-first eviction.
- Consider making eviction resistant to adversarial timing, e.g., evicting oldest expired-but-near-timeout entries with randomized jitter, or rejecting new insertions once at capacity (return a "server busy" error) rather than silently evicting others' pending entries.

### Proof of Concept
Go unit test plan (extends existing `TestPruneCallbacks` in `handler_test.go`):
1. Set `handler.config.MaxSavedCallbacks = N` (e.g., 10).
2. Insert one "victim" `savedCallback` with `createdAt = now.Add(-1*time.Second)` under a distinct ID (`"victim"`).
3. Insert `N` "attacker" `savedCallback`s with `createdAt = now` (later than victim) under IDs `"attacker-0"` ... `"attacker-N-1"`, simulating a flood via repeated `HandleLegacyUserMessage` calls with unique `MessageId`s and valid signatures from an arbitrary throwaway key (not a DON member key).
4. Call `handler.pruneCallbacks()`.
5. Assert `handler.savedCallbacks` no longer contains `"victim"` (deterministically evicted because it is oldest) while newer attacker-inserted entries survive — confirming targeted eviction is deterministic and attacker-controlled, not random.
6. Optionally extend to full integration: call `handler.HandleLegacyUserMessage` for the victim first, then flood via the same call for the attacker entries, then assert `victim`'s callback channel receives no response and eventually times out, confirming the response-drop end-to-end.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L314-334)
```go
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-414)
```go
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
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
