### Title
Legacy Web API Trigger messages are freely replayable within the staleness window, allowing duplicate workflow executions - (File: core/services/gateway/handlers/capabilities/handler.go, core/capabilities/webapi/trigger/trigger.go)

### Summary
The ECO bug allowed a permissionless `rebase` call to be replayed with a stale value because the L1→L2 message carried no monotonic/one-time-use marker, only content that could be resubmitted. The Chainlink gateway's legacy Web API trigger path has an analogous gap: an unprivileged, signed user message is accepted as long as it is not older than `MaxAllowedMessageAgeSec`, but nothing prevents the exact same signed message from being submitted (or forwarded to the DON) multiple times within that window, or from being replayed by the DON-side handler once the gateway forwards it.

### Finding Description
`HandleLegacyUserMessage` in [1](#0-0)  validates a user-supplied `api.Message` only by checking `payload.Timestamp` against `MaxAllowedMessageAgeSec` (a pure staleness check), then forwards the message unmodified to every DON member: [2](#0-1) 

There is no digest/nonce replay guard here — contrast this with the Vault gateway path, which explicitly implements `RequestReplayGuard`/`jwtReplayCache` to reject already-seen digests [3](#0-2)  and the newer HTTP trigger handler which tracks JWT `jti` reuse [4](#0-3) . The legacy path has no such protection.

On the node side, `triggerConnectorHandler.HandleGatewayMessage` / `processTrigger` in [5](#0-4)  derives a `TriggerEventID` from `body.Sender + payload.TriggerEventId` [6](#0-5) , but this ID is only used for observability/event emission — it is not checked against a de-duplication cache before firing the trigger channel (`trigger.ch <- tr`) at [7](#0-6) . The only defense against repeated firing is the per-sender rate limiter (`trigger.rateLimiter.Allow(body.Sender)`), which throttles frequency but does not prevent an old, previously-processed message from being resubmitted and accepted again as long as its rate budget allows and the timestamp is still within the configured window.

### Impact Explanation
An unprivileged party who has observed/captured one valid signed trigger message (e.g. via the DON gateway's own response echo, logs, or by being the original legitimate sender) can resubmit that exact message multiple times during the staleness window. Each successful resubmission re-fires `RegisterTrigger`'s channel and re-triggers workflow execution for every workflow matching the topic/sender, generating duplicate workflow runs. If the downstream workflow performs value-bearing actions (fund transfers, on-chain writes, external side effects) keyed off the trigger event rather than a globally-enforced one-time nonce, this enables duplicate execution/fund movement analogous to the ECO rebase replay, where a stale-but-still-parseable message was reprocessed to desynchronize protocol state and profit.

### Likelihood Explanation
Likelihood is moderate: the attacker does not need special privileges — only a previously-seen valid signed message payload and the ability to resend it to the gateway HTTP endpoint before it exceeds `MaxAllowedMessageAgeSec` (or to hit the DON directly, if the network allows). It requires the workflow's own trigger handler logic to be non-idempotent, so the actual severity depends on how each downstream workflow treats `TriggerEventId`.

### Recommendation
Add a replay/dedup guard on the legacy webapi trigger path analogous to the Vault `RequestReplayGuard`: reject messages whose `MessageId`/`TriggerEventId` (or content digest) has already been accepted within the staleness window, both at the gateway (`HandleLegacyUserMessage`) and at the DON handler (`processTrigger`) before firing the trigger channel. Persist a bounded, time-expiring seen-set keyed by `body.Sender + payload.TriggerEventId` and short-circuit duplicate deliveries, mirroring the existing `jwtReplayCache`/`RequestReplayGuard` pattern already used elsewhere in the gateway code.

### Proof of Concept
1. Register a webapi-trigger workflow with `AllowedSenders` including attacker's signing key and a topic.
2. Attacker crafts and signs a valid `api.Message` with `MethodWebAPITrigger`, a fresh `Timestamp`, and `TriggerEventId = "X"`.
3. Attacker POSTs this message to the gateway; `HandleLegacyUserMessage` passes the staleness check and forwards to all DON members; `processTrigger` fires the trigger channel, causing one workflow execution.
4. Before `Timestamp` exceeds `MaxAllowedMessageAgeSec`, attacker resubmits the identical message bytes/signature to the gateway again (and/or to DON nodes if reachable). Because neither `HandleLegacyUserMessage` nor `processTrigger` checks for a previously-seen `MessageId`/`TriggerEventId`, the staleness check still passes and `processTrigger` fires the trigger channel again, producing a second workflow execution from the same original signed request — subject only to the sender rate limiter's burst/RPS budget.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-384)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/capabilities/vault/request_replay_guard.go (L9-47)
```go
var ErrRequestAlreadySeen = errors.New("request was already authorized previously")

// RequestReplayGuard prevents replay of already-processed requests by tracking
// request digests with expiry timestamps. It is safe for concurrent use.
//
// Used by both the AllowListBasedAuth flow and the JWTBasedAuth flow to ensure
// that a given request digest is only accepted once.
type RequestReplayGuard struct {
	mu      sync.Mutex
	seen    map[string]int64 // digest → unix expiry timestamp
	nowFunc func() time.Time // injectable for testing
}

// NewRequestReplayGuard creates a replay guard for authorized Vault requests.
func NewRequestReplayGuard() *RequestReplayGuard {
	return &RequestReplayGuard{
		seen:    make(map[string]int64),
		nowFunc: time.Now,
	}
}

// CheckAndRecord returns ErrRequestAlreadySeen if the digest was previously
// recorded and has not yet expired. Otherwise it records the digest with
// the given expiry timestamp (unix seconds, UTC).
//
// Expired entries are cleaned up on every call.
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
```

**File:** core/capabilities/webapi/trigger/trigger.go (L80-149)
```go
func (h *triggerConnectorHandler) processTrigger(ctx context.Context, gatewayID string, body *api.MessageBody, sender ethCommon.Address, payload webapicap.TriggerRequestPayload) error {
	// Pass on the payload with the expectation that it's in an acceptable format for the executor
	wrappedPayload, err := values.WrapMap(payload)
	if err != nil {
		return fmt.Errorf("error wrapping payload %w", err)
	}
	topics := payload.Topics

	// empty topics is error for V1
	if len(topics) == 0 {
		return errors.New("empty Workflow Topics")
	}

	// workflows that have matched topics
	matchedWorkflows := 0
	// workflows that have matched topic and passed all checks
	fullyMatchedWorkflows := 0
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
				fullyMatchedWorkflows++
				TriggerEventID := body.Sender + payload.TriggerEventId

				// Emit trigger execution started event
				workflowExecutionID, genErr := events.GenerateExecutionID(trigger.workflowID, TriggerEventID)
				if genErr != nil {
					h.lggr.Errorw("failed to generate execution ID", "err", genErr)
					workflowExecutionID = ""
				}
				emitErr := events.EmitTriggerExecutionStarted(ctx, map[string]string{}, TriggerEventID, workflowExecutionID)
				if emitErr != nil {
					h.lggr.Errorw("failed to emit trigger execution started event", "err", emitErr)
				}

				tr := capabilities.TriggerResponse{
					Event: capabilities.TriggerEvent{
						TriggerType: TriggerType,
						ID:          TriggerEventID,
						Outputs:     wrappedPayload,
					},
				}
				select {
				case <-ctx.Done():
					return nil
				case trigger.ch <- tr:
					// Sending n topics that match a workflow with n allowedTopics, can only be triggered once.
					break
				}
			}
		}
	}
	if matchedWorkflows == 0 {
		return errors.New("no Matching Workflow Topics")
	}

	if fullyMatchedWorkflows > 0 {
		return nil
	}
	return err
}
```
