### Title
Unbounded `savedCallbacks` map growth under attacker-controlled `MessageId` allows availability degradation for legitimate trigger callbacks - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` inserts a new entry into `h.savedCallbacks` for every valid, signed `MethodWebAPITrigger` message without any bound check or per-sender quota, and the code explicitly marks rate-limiting/allowlisting as a TODO. `pruneCallbacks` only runs on a periodic tick (`CallbackPruneIntervalSec`, default 30s) and evicts indiscriminately by age (oldest half) once the map exceeds `MaxSavedCallbacks` (default 20000), so a burst of many distinct signed requests within one tick interval can grow the map far beyond its intended bound and can cause legitimate, concurrently-submitted callbacks to be evicted before their corresponding node response arrives.

### Finding Description
`HandleLegacyUserMessage` <cite repo="ThankGod76/chainlink--004" path="core/services/gateway/handlers/capabilities/handler.go" start="341,411" end="356,414" /> validates the message (signature length, JSON decoding, timestamp freshness) via `msg.Validate()`/`ExtractSigner` in `api/message.go`, but the message signature only proves the request came from *some* keypair that the attacker controls — signing costs nothing and the sender is not otherwise authenticated, rate-limited, or checked against an allowlist. The comment at line 384 explicitly states: `// TODO: apply allowlist and rate-limiting here` [1](#0-0) .

After validation, every request unconditionally inserts into the shared map keyed only by attacker-supplied `MessageId`:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [2](#0-1) 

There is no check of `len(h.savedCallbacks)` at insertion time — the map is allowed to grow without limit between prune ticks. `pruneCallbacks` runs only every `CallbackPruneIntervalSec` (default 30s) [3](#0-2) , and when it does run, if the map exceeds `MaxSavedCallbacks` it sorts *all* entries by `createdAt` and deletes the oldest half regardless of which sender created them [4](#0-3) . Because eviction is purely time-based and not sender-aware, a legitimate victim's callback registered just before or during an attacker's burst can be evicted along with (or instead of) the attacker's flood of entries, and the corresponding node response in `handleWebAPITriggerMessage` will silently find `found == false` and drop the response [5](#0-4) .

### Impact Explanation
This is an availability/isolation issue: an attacker who can produce arbitrarily many distinct signed `MethodWebAPITrigger` messages (signing is cheap, requires no privilege beyond possessing any keypair) can flood `savedCallbacks` with far more than `MaxSavedCallbacks` entries inside a single 30-second prune window, causing indiscriminate age-based eviction to potentially discard legitimate victims' pending callbacks before the DON nodes' responses arrive, resulting in silently dropped trigger responses for legitimate users. This matches a scoped "denial of legitimate service to other users" impact class rather than a node compromise, credential leak, or fund-movement bug.

### Likelihood Explanation
Preconditions are low: the attacker only needs the ability to construct and sign arbitrary `api.Message` objects (any ECDSA keypair works — `ExtractSigner` only recovers *an* address, it does not check that address against any allowlist inside `HandleLegacyUserMessage`) and network reachability to the gateway's user-message endpoint. The attack is repeatable indefinitely and requires no special role. However, I could not fully verify from the indexed files whether an allowlist or additional authentication is enforced *upstream* of `HandleLegacyUserMessage` (e.g., in the HTTP transport layer or gateway routing before dispatch) — the TODO comment and the absence of any allowlist/rate-limit code inside this function strongly suggest none exists at this layer, but I was not able to trace the full HTTP-to-handler dispatch path within the available index to rule out an external control.

### Recommendation
- Enforce a per-sender (per-`msg.Body.Sender`) quota/rate limit before inserting into `savedCallbacks`, reusing the existing `ratelimit.RateLimiter` pattern already used for `nodeRateLimiter`.
- Reject or apply backpressure to new insertions once `len(h.savedCallbacks) >= MaxSavedCallbacks` instead of only reactively evicting on the periodic tick.
- When evicting under pressure, prefer evicting the sender(s) contributing disproportionately many entries rather than a purely age-based cut, to preserve isolation between users.
- Implement the allowlist/rate-limiting TODO referenced at line 384.

### Proof of Concept
Go unit test plan (extends `handler_test.go`):
1. Construct a `handler` with `MaxSavedCallbacks = 20`, `CallbackPruneIntervalSec` set high enough that the prune ticker does not fire during the test (or call `pruneCallbacks()` manually only once at the end).
2. In a loop, call `HandleLegacyUserMessage` 1000 times with distinct signed messages (unique `MessageId`s, valid signatures from distinct or a single throwaway key) each carrying its own `handlers.Callback` mock, and confirm each call succeeds without any rejection (assert no error, and each callback registered `savedCallbacks` entry present immediately after insertion, i.e. `len(h.savedCallbacks)` grows past `MaxSavedCallbacks` before any prune runs).
3. Before the prune tick fires, submit one additional "victim" `HandleLegacyUserMessage` call and immediately trigger `handleWebAPITriggerMessage` for it with the correct `MessageId` from a simulated node response; assert the victim's callback fires correctly (`SendResponse` invoked) — establishing baseline.
4. Then simulate the flood entirely completing before the victim's node response returns, call `pruneCallbacks()` once with the map now containing `> MaxSavedCallbacks` entries, and assert whether the victim's still-pending callback (registered at a timestamp within the oldest half of the flood) has been evicted from `savedCallbacks` — i.e., assert `_, found := h.savedCallbacks[victimMessageId]; !found`, and that subsequently calling `handleWebAPITriggerMessage` for the victim's ID returns `found=false` and never invokes `SendResponse`, demonstrating dropped response to a legitimate user caused by unbounded/indiscriminate map growth and eviction.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L269-284)
```go
func (h *handler) Start(context.Context) error {
	return h.StartOnce(handlerName, func() error {
		h.wg.Go(func() {
			ticker := time.NewTicker(time.Duration(h.config.CallbackPruneIntervalSec) * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					h.pruneCallbacks()
				case <-h.stopCh:
					return
				}
			}
		})
		return nil
	})
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-412)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
```
