### Title
Unbounded `topics` array in `web_api_trigger` payload causes O(topics × registered workflows) CPU-bound loop in gateway connector trigger handler - (File: core/capabilities/webapi/trigger/trigger.go)

### Summary
The `web_api_trigger` message path from an unprivileged client (through the internet-facing gateway) into the DON node's `triggerConnectorHandler` accepts a `TriggerRequestPayload` whose `topics` field is an unbounded JSON array. `processTrigger` nests iteration over this attacker-controlled array inside a loop over every registered workflow, with no cap on array size or total iteration count, mirroring the unbounded-array bug class described in the `GovernorAlpha` report (unbounded `targets`/`values`/etc. arrays causing runaway iteration in `queue`/`cancel`/`execute`).

### Finding Description
`processTrigger` reads `payload.Topics` directly from the untrusted JSON payload and iterates:
```go
for _, trigger := range h.registeredWorkflows {
    for _, topic := range topics {
        if trigger.allowedTopics[topic] { ... }
    }
}
``` [1](#0-0) 
The JSON schema for `TriggerRequestPayload.topics` only declares `type: array, items: {type: string}` with no `maxItems` constraint: [2](#0-1) 
The payload is unmarshalled straight from `msg.Body.Payload` in `HandleGatewayMessage` without any size/length validation on `Topics` before being passed to `processTrigger`: [3](#0-2) 
Unlike other request types in the same package (e.g., Vault's `EncryptedSecret`/`SecretIdentifier` batches, which are explicitly capped at `vaulttypes.MaxBatchSize`), there is no equivalent cap for the number of topics in a single trigger request: [4](#0-3) 
This is directly analogous to the `GovernorAlpha.propose` bug class: an unbounded, caller-supplied array is iterated by downstream logic (`queue`/`cancel`/`execute` in the original report; `processTrigger`'s nested loop here) without any hard cap, allowing a single request to blow up the amount of work performed.

### Impact Explanation
Because the outer loop ranges over every workflow currently registered with `h.registeredWorkflows` (all workflows registered to this DON/gateway pairing for the `web_api_trigger` capability) and the inner loop ranges over the caller-supplied `topics` array, a malicious unprivileged client can submit a single `web_api_trigger` message with an extremely large `topics` array (bounded only by the practical message-size limits of the transport) to multiply CPU work by `len(topics) * len(registeredWorkflows)`. This can degrade or stall processing of the gateway connector's message-handling goroutine for legitimate workflow triggers on that node — a resource-exhaustion/availability impact analogous to (though less severe in blast radius than) the on-chain gas-exhaustion described in the source report, since it is reachable by an unauthenticated/unprivileged actor able to reach the gateway.

### Likelihood Explanation
Reaching this code path requires only that the gateway forward a `web_api_trigger` method message to a connected node with at least one workflow subscribed to that trigger — a routine, low-privilege interaction that doesn't require prior authorization beyond being able to submit a request that is routed to the DON as a trigger event. No additional cap on `topics` length exists in validation prior to `processTrigger`, making the trigger straightforward for anyone able to send messages to this handler.

### Recommendation
Enforce a hard maximum on the number of elements accepted in `TriggerRequestPayload.topics` (analogous to `vaulttypes.MaxBatchSize` used elsewhere in the codebase) — reject requests exceeding the cap before they reach `processTrigger`, and add a corresponding `maxItems` constraint to `event_trigger-schema.json`.

### Proof of Concept
I could not construct and verify a concrete end-to-end exploit trace confirming the absence of any upstream size limiter (e.g., total message-size caps enforced elsewhere in the gateway HTTP/WS layer) that might already bound the practical size of `topics` in production deployments; grep for request/body size guards in `core/services/gateway/network/httpserver.go` returned a single match whose enforced limit I was unable to fully inspect within the available context. This should be verified in a live/Devin session by (1) tracing the exact byte-size cap enforced on gateway HTTP/WS request bodies, and (2) sending a crafted `web_api_trigger` JSON-RPC request with a very large `topics` array (up to that byte limit) to a node with several registered workflows, measuring CPU time spent in `processTrigger` versus a baseline single-topic request.

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L96-99)
```go
	fullyMatchedWorkflows := 0
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
```

**File:** core/capabilities/webapi/trigger/trigger.go (L151-172)
```go
func (h *triggerConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		h.lggr.Errorw("error validating message from request", "err", err, "request", req)
		return nil
	}
	body := &msg.Body
	sender := ethCommon.HexToAddress(body.Sender)
	var payload webapicap.TriggerRequestPayload
	err = json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw("error decoding payload", "err", err)
		err = h.sendResponse(ctx, gatewayID, body, ghcapabilities.TriggerResponsePayload{Status: "ERROR", ErrorMessage: fmt.Errorf("error %s decoding payload", err.Error()).Error()})
		if err != nil {
			h.lggr.Errorw("error sending response", "err", err)
		}
		return nil
	}

	switch body.Method {
	case ghcapabilities.MethodWebAPITrigger:
		resp := h.processTrigger(ctx, gatewayID, body, sender, payload)
```

**File:** core/capabilities/webapi/webapicap/event_trigger-schema.json (L69-75)
```json
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description" : "An array of a single topic (string) to be started by this event."
                    }
                },
```

**File:** core/capabilities/vault/validate_user_request_test.go (L221-245)
```go
func TestRequestValidator_ValidateCreateSecretsRequest_RejectsBatchAboveLimit(t *testing.T) {
	t.Parallel()

	validator, err := vault.NewRequestValidatorFromLimitsFactory(limits.Factory{Settings: cresettings.DefaultGetter})
	require.NoError(t, err)

	owner := "0xabc"
	encryptedSecrets := make([]*vaultcommon.EncryptedSecret, vaulttypes.MaxBatchSize+1)
	for i := range encryptedSecrets {
		encryptedSecrets[i] = &vaultcommon.EncryptedSecret{
			Id: &vaultcommon.SecretIdentifier{
				Key:   fmt.Sprintf("key%d", i),
				Owner: owner,
			},
			EncryptedValue: "ab",
		}
	}

	err = validator.ValidateCreateSecretsRequest(t.Context(), nil, &vaultcommon.CreateSecretsRequest{
		RequestId:        "req-create-over-limit",
		EncryptedSecrets: encryptedSecrets,
	}, true)
	require.Error(t, err)
	require.ErrorContains(t, err, fmt.Sprintf("request batch size exceeds maximum of %d", vaulttypes.MaxBatchSize))
}
```
