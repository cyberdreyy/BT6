### Title
Cross-workflow response confusion due to MessageId-only callback keying without DonId/workflow-ownership verification - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`HandleLegacyUserMessage` stores each pending callback in `h.savedCallbacks` keyed **only** by `msg.Body.MessageId`, with no binding to a workflow ID, DON shard, or requester identity beyond that string key: [1](#0-0) 

When a node response for `MethodWebAPITrigger` arrives, `handleWebAPITriggerMessage` looks the callback up purely by `msg.Body.MessageId`, deletes it, and forwards the raw node response to whatever callback is currently stored under that key — it never checks `msg.Body.DonId` or any workflow-identifying field against the metadata that was present at registration time: [2](#0-1) 

The only sender-side check performed in `HandleNodeMessage` is `msg.Body.Sender != nodeAddr`, which authenticates that the *node* is who it claims to be on the websocket connection, but says nothing about which workflow/request the message is a response to: [3](#0-2) 

Each `handler` instance (and its `savedCallbacks` map) is instantiated once per DON via `handlerFactory.NewHandler` / `capabilities.NewHandler`, so all workflow triggers routed through the same DON share a single map keyed by `MessageId`: [4](#0-3) 

`MessageId` is taken directly from the inbound `api.Message.Body.MessageId` supplied by the caller of `HandleLegacyUserMessage` (i.e., whatever produced the trigger request reaching the gateway, such as an external-initiator/bridge-originated flow) — the gateway does not generate or enforce global uniqueness of this identifier itself, nor does it validate a workflow-ownership field on the return path. Consequently, if two concurrent trigger requests for different workflows on the same DON happen to use the same `MessageId` string (accidental collision or attacker-influenced value on a low-trust ingestion path), the second `h.savedCallbacks[msg.Body.MessageId] = ...` assignment silently overwrites the first pending callback. Any subsequent node response matching that `MessageId` is then delivered to the second (attacker- or unrelated-) workflow's callback, and the first workflow's original callback is lost/never invoked correctly — this is a violation of the "one request bound to exactly one workflow" invariant.

### Impact Explanation
This falls into the "cross-user response confusion" bounty class: a workflow-triggering response intended for workflow A could instead be delivered to (or overwritten by) a callback registered for workflow B, causing incorrect job-triggering outcomes, response mis-delivery, or denial of the original workflow's trigger response. Impact is limited to the response-routing layer (a single DON's `web_api_trigger` callback map) and does not expose secrets, since payload content is still the node's own signed message; but it does break the request/response integrity guarantee that a specific `MessageId` corresponds to exactly one waiting workflow.

### Likelihood Explanation
Exploitability depends entirely on whether `MessageId` values reaching `HandleLegacyUserMessage` can collide across concurrently pending requests — the gateway code itself performs no uniqueness enforcement or ownership check on this field. If any low-trust upstream component (external initiator, bridge, or workflow SDK) can be influenced to reuse or predict a `MessageId`, the collision is trivially triggerable by submitting two workflow-trigger requests with the same ID while both are still pending (within `CallbackMaxAgeSec`, default 120s). No node compromise is required — only control over the `MessageId` value of an inbound trigger request, which is consistent with the stated attacker model (compromised low-trust bridge/EI feeding gateway ingestion).

### Recommendation
Bind the saved callback to more than the bare `MessageId`: derive/verify the map key (or store alongside it) using `DonId` plus a workflow/owner identifier extracted from the trigger payload, and reject/overwrite-guard registrations that reuse an in-flight `MessageId` for a different owner. On the response path, validate that the responding message's `DonId`/workflow metadata matches what was recorded at registration time before invoking `savedCb.SendResponse`, returning an error (and dropping the stale callback) on mismatch instead of silently delivering it.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a DON config and no-op `handlers.DON`.
2. Register callback #1 via `HandleLegacyUserMessage` for "workflow A" using `MessageId = "dup-id"`, with a `callback.SendResponse` spy that records the response body.
3. Before callback #1's node responds, register callback #2 via `HandleLegacyUserMessage` for "workflow B" using the same `MessageId = "dup-id"`, with a distinct spy.
4. Assert `len(h.savedCallbacks) == 1` and that it now belongs to workflow B's spy (overwrite confirmed).
5. Simulate a node response for `MethodWebAPITrigger` with `Body.MessageId = "dup-id"` via `HandleNodeMessage`.
6. Assert workflow B's spy received the response and workflow A's spy never fires — demonstrating a response can be captured by an unrelated workflow's callback due to `MessageId`-only keying, with no `DonId`/ownership check present in `handleWebAPITriggerMessage`.

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

**File:** core/services/gateway/handler_factory.go (L80-82)
```go
		return handlers.NewDummyHandler(donConfig, don, hf.lggr)
	case WebAPICapabilitiesType:
		return capabilities.NewHandler(handlerConfig, donConfig, don, hf.httpClient, hf.lggr)
```
