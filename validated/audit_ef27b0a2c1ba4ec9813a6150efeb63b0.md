### Title
Cross-User Response Hijacking via Unbound, Client-Controlled MessageId in Gateway Legacy Handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Gateway's legacy web-API handler stores a pending user callback in a shared map keyed **only** by the client-supplied `MessageId`, with no binding to the requesting client's identity or session. Because `MessageId` is fully attacker-controlled and its uniqueness is never enforced, an unprivileged client can choose a `MessageId` that collides with another in-flight request, overwrite that request's callback slot, and receive the DON node's response meant for the other user (or cause the other user's response to be delivered into the attacker's slot). This is conceptually the same bug class as the reported flash-loan reentrancy issue: a piece of shared, privileged state (there: `executeOperation`'s execution permission on the DSProxy; here: the `savedCallbacks[MessageId]` response-delivery slot) is left open/mutable across an untrusted external interaction without any mutex/ownership binding, allowing an attacker to inject themselves into another party's in-progress execution.

### Finding Description
`HandleLegacyUserMessage` accepts a message from an unprivileged client (reached via `gateway.ProcessRequest` → `g.handlers[...].HandleLegacyUserMessage`) and stores the callback keyed purely by the client-chosen `msg.Body.MessageId`: [1](#0-0) 

`MessageId` validation only checks length/format/null-suffix — it does not enforce uniqueness or bind the ID to the sender: [2](#0-1) 

Later, when a DON node responds with the same method/ID, `handleWebAPITriggerMessage` looks up and deletes whatever callback is currently stored under that `MessageId`, and delivers the response to it — with no check that the responding node's answer corresponds to the same client that originally submitted the request: [3](#0-2) 

If two clients (or the same attacker with repeated requests) submit requests using the same `MessageId` while the first is still pending (up to `CallbackMaxAgeSec`, default 120s, before pruning), the second `h.savedCallbacks[msg.Body.MessageId] = ...` assignment silently overwrites the first entry: [4](#0-3) 

This mirrors the reported bug class: a temporary, privileged "callback"/execution slot remains open to be hijacked by an external, attacker-timed interaction because no lock/ownership check is enforced for the duration of the pending operation.

### Impact Explanation
An attacker who predicts or brute-forces another client's `MessageId` (or simply races legitimate traffic with a chosen ID) can capture the DON's response intended for a victim's web-API trigger request, and simultaneously deny the victim their real response (since the map is overwritten and the victim's callback is dropped, only firing on prune/timeout with an internal error, or never at all if the attacker's slot consumes it first). This is a cross-user response confusion vulnerability reachable purely by unprivileged HTTP/gateway clients, without needing to compromise a node or the network layer.

### Likelihood Explanation
Likelihood depends on the difficulty of guessing another client's `MessageId` and winning the race window before the legitimate node response arrives. If callers use predictable IDs (sequential counters, timestamps, or short strings within the 128-byte limit), collision is trivial; even with random IDs, deliberate self-collision by the same attacker across their own concurrent requests can still be used to test/manipulate delivery ordering. The core defect — no per-sender ownership check — is deterministic and always exploitable given ID collision.

### Recommendation
Bind each saved callback entry to the authenticated sender/session, not just the raw `MessageId` (e.g., composite key of `Sender + MessageId`, or store sender inside `savedCallback` and verify it matches before invoking `SendResponse`). Reject or reuse-detect duplicate in-flight `MessageId`s from the same DON/method to prevent silent overwrites, analogous to adding a mutex/ownership guard around the callback lifecycle the way `ReentrancyGuard` was added to guard `executeOperation` end-to-end in the referenced fix.

### Proof of Concept
1. Attacker submits a legacy webhook/trigger request through the gateway with `Body.MessageId = "X"`, self-signed with their own key so it passes `Validate()`.
2. Concurrently, a victim submits a legitimate request that (by prediction, collision, or coincidence) also uses `MessageId = "X"`.
3. Whichever request's `h.savedCallbacks["X"] = &savedCallback{...}` executes last overwrites the other in the shared map (`core/services/gateway/handlers/capabilities/handler.go:412`).
4. When the DON node responds with `MethodWebAPITrigger` and `MessageId = "X"`, `handleWebAPITriggerMessage` (`handler.go:148-162`) delivers the result to whichever callback remains — potentially the attacker's — regardless of which client actually initiated the underlying DON computation.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
