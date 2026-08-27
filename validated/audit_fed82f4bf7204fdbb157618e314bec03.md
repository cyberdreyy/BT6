Important finding: `msg.Validate()` only requires a valid ECDSA signature over the message body — the signer can be **any freshly generated keypair**, not a pre-registered/allowlisted address. `Message.Validate()` calls `ExtractSigner()` which recovers whatever address signed the payload, with no check against a known-sender allowlist at this layer [1](#0-0) . Combined with the explicit `// TODO: apply allowlist and rate-limiting here` comment in `HandleLegacyUserMessage` [2](#0-1) , an unprivileged, unauthenticated attacker can generate unlimited keypairs, sign unlimited distinct `web_api_trigger` messages, and call the gateway's public HTTP endpoint (`gateway.ProcessRequest` → `HandleLegacyUserMessage`) with no per-sender or global rate limit at this stage.

### Title
Unbounded `savedCallbacks` map allows unprivileged flooding to evict legitimate pending callbacks, causing cross-user request starvation - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` inserts every valid (signature-checked but otherwise unauthenticated) trigger request into the shared `h.savedCallbacks` map without any allowlist or per-sender rate limiting, and `pruneCallbacks` evicts the globally oldest entries down to `MaxSavedCallbacks/2` whenever the map grows too large. An attacker who floods the gateway with signed but otherwise arbitrary `web_api_trigger` requests can force eviction of another legitimate, still-pending user's callback before the DON nodes respond.

### Finding Description
The attack path is: HTTP request → `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) → `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`). The only checks performed are payload decoding, timestamp/staleness checks, method-name check, and `common.ValidatedRequestFromMessage` (signature validation) — none of which restrict *who* can send a message, since any self-generated ECDSA key produces a valid signature [3](#0-2) . Immediately after validation, the handler unconditionally stores the callback keyed by `MessageId`: [4](#0-3) 

`pruneCallbacks` runs on a timer (`CallbackPruneIntervalSec`, default 30s) and, after removing expired entries, sorts all remaining entries by `createdAt` and deletes the oldest half if the map exceeds `MaxSavedCallbacks` (default 20000): [5](#0-4) 

This eviction is global and indiscriminate — it has no notion of "sender" or "victim," it just kills the oldest half of *all* pending callbacks in the DON handler, regardless of which unauthenticated caller registered them. A victim's callback that was legitimately registered slightly earlier than an attacker's flood can be deleted with no `SendResponse` call, silently dropping it from the map (line 331: `delete(h.savedCallbacks, e.id)` with no callback invocation). The test suite confirms `SendResponse` is never called on eviction paths other than through the map's normal winning response flow or explicit error handling [6](#0-5) , and the code itself documents the missing control: `// TODO: apply allowlist and rate-limiting here` [2](#0-1) .

The victim does not hang forever, however: `gateway.ProcessRequest` waits on `callback.Wait(ctx)` using the HTTP request's own context, and if the callback is evicted (never called), the context eventually expires and the caller receives an internally generated `RequestTimeoutError`/`"handler timeout"` response [7](#0-6) . So the practical effect is a forced timeout/error for a request that would otherwise have succeeded — not an indefinite silent hang — but it is still a genuine denial-of-isolation between unrelated unprivileged callers of the same DON handler.

### Impact Explanation
This is a Denial of Service / cross-user isolation failure scoped to a single DON's `WebAPIHandler` instance: an unprivileged attacker can cause a legitimate victim's otherwise-successful `web_api_trigger` request to fail with a timeout error instead of returning the real DON response. It does not enable authentication bypass, secret disclosure, unauthorized fund movement, or privilege escalation — its impact is limited to request-availability degradation for other callers sharing the DON handler, which maps to a lower-severity availability/DoS impact class rather than a critical compromise. Reaching the required scale (tens of thousands of distinct valid signed messages within one prune cycle) requires substantial throughput from the attacker, and default reasonable HTTP server-level throttling (if configured) would raise the bar further, though such upstream throttling is out of scope for this specific function per the audit rules.

### Likelihood Explanation
No credential, allowlist membership, or role is required — a bare `ecdsa` keypair the attacker generates locally is sufficient, since `Message.Validate()`/`ExtractSigner()` accept any signer [8](#0-7) . However, to actually trigger the described eviction of a specific victim's callback, the attacker must (a) know or guess the victim is mid-request, and (b) generate enough distinct, validly-signed, non-stale messages to push `len(savedCallbacks)` above `MaxSavedCallbacks` (20000 by default) within the `CallbackMaxAgeSec` (120s) / `CallbackPruneIntervalSec` (30s) window so the victim's entry is not already naturally expired-and-pruned first. This is feasible for a well-resourced flooder given the noted absence of per-sender rate limiting at this layer, but it is a volumetric, probabilistic attack (targeting a *specific* victim message and timing eviction precisely is much harder than causing generic churn/degradation).

### Recommendation
Implement per-sender rate limiting and/or an allowlist check in `HandleLegacyUserMessage` before inserting into `savedCallbacks` (the code already has a `nodeRateLimiter` pattern used elsewhere in the file that could be adapted for user-facing requests). Additionally, consider bounding insertion (reject/backpressure new requests when `savedCallbacks` is already at `MaxSavedCallbacks`, rather than admitting unlimited entries and evicting older legitimate ones), and/or making eviction fairness-aware (e.g., per-sender caps) rather than pure global oldest-first eviction.

### Proof of Concept
Go handler-level integration test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with a small `MaxSavedCallbacks` (e.g., 4) and `CallbackPruneIntervalSec` set low for test speed.
2. Register a "victim" callback via `handler.HandleLegacyUserMessage` with a valid signed trigger message from key A; do not resolve it (don't call `handleWebAPITriggerMessage` for it yet).
3. Loop: generate N new random ECDSA keys, sign N distinct valid trigger messages, and call `handler.HandleLegacyUserMessage` for each, filling `savedCallbacks` past `MaxSavedCallbacks` before the victim's is expired by age.
4. Manually invoke `handler.pruneCallbacks()` (or wait for the ticker).
5. Assert that the victim's `MessageId` is no longer present in `handler.savedCallbacks` (eviction occurred) and that `cb.Wait(ctx)` for the victim never receives a `SendResponse` call (times out), demonstrating the victim's callback was silently dropped rather than resolved by the actual DON response.

### Citations

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

**File:** core/services/gateway/api/message.go (L96-108)
```go
func (m *Message) Sign(privateKey *ecdsa.PrivateKey) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := gw_common.SignData(privateKey, rawData...)
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(crypto.PubkeyToAddress(privateKey.PublicKey).Hex())
	return nil
}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L338-363)
```go
	})
	t.Run("savedCallbacks stored only when message is valid", func(t *testing.T) {
		require.Empty(t, handler.savedCallbacks)

		invalidPayloadMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidPayloadMsg, cb)
		require.NoError(t, err)
		_, _ = cb.Wait(t.Context())

		staleMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb2 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, staleMsg, cb2)
		require.NoError(t, err)
		_, _ = cb2.Wait(t.Context())

		badMethodMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb3 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, badMethodMsg, cb3)
		require.NoError(t, err)
		_, _ = cb3.Wait(t.Context())

		handler.mu.Lock()
		require.Empty(t, handler.savedCallbacks, "error paths must not leave entries in savedCallbacks")
		handler.mu.Unlock()
	})
```

**File:** core/services/gateway/gateway.go (L278-285)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
```
