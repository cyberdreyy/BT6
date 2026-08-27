### Title
Integer sign-to-unsigned wraparound lets an unprivileged sender permanently defeat the Gateway's message-freshness (anti-replay) check - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Gateway's legacy Web API trigger handler validates message freshness by mixing a signed 64-bit integer (`payload.Timestamp`, attacker-supplied) with unsigned arithmetic. A crafted negative timestamp wraps around to a huge `uint` value that always satisfies the "not stale" branch, exactly analogous to the reported `LiquidityPool.getNewCurrentFees` bug where mixing two independently-derived values in a bare subtraction/comparison breaks the intended safety property (there it caused a revert/DoS; here it silently disables a protection check).

### Finding Description
`HandleLegacyUserMessage` decodes an externally supplied, signed `TriggerRequestPayload` whose `Timestamp` field is a plain `int64`: [1](#0-0) 

The freshness check only rejects `Timestamp == 0`, then performs the staleness comparison entirely in `uint`: [2](#0-1) 

The comparison is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // stale message, reject
}
```
`payload.Timestamp` is fully attacker-controlled (it's part of the JSON payload the client signs, per `msg.Sign(key)` in the test helper `triggerRequest`) [3](#0-2) . If a sender sets `Timestamp` to a negative value (e.g., `-1`), `uint(payload.Timestamp)` wraps to a value close to `2^64-1` (or `2^32-1` on 32-bit `uint`), which will always be larger than `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`. The staleness check therefore can never trigger for such a message, regardless of how old the actual request is when it is (re)played.

This mirrors the `getNewCurrentFees` root cause exactly: the code assumes `feeRatio`/`timestamp` values always sit in a "safe" ordering relative to the other operand, and once that assumption is broken (by governance changing `MinWithdrawalFee`, here by an attacker choosing a negative timestamp), the unsigned/plain arithmetic produces a value outside the intended domain, silently breaking a safety invariant instead of raising a clean error.

### Impact Explanation
The `MaxAllowedMessageAgeSec` freshness check exists specifically to bound how long a signed `web_api_trigger` message remains acceptable to the DON (see the schema comment: "needs to be within certain freshness to be processed") [4](#0-3) . A message with a negative `Timestamp` permanently defeats this bound: it will never be classified as stale by the gateway, so a single captured signed message can be relayed/forwarded to the DON members indefinitely (subject only to node rate limiting and per-message-ID callback dedup, which do not prevent the DON nodes themselves from re-processing forwarded copies). This weakens the intended anti-replay/staleness guarantee that gates unauthorized/aged trigger requests from reaching workflow-triggering nodes, i.e., a legitimate DoS/replay-window control on an internet-facing gateway path is silently disabled for a crafted request.

### Likelihood Explanation
Reachable by any unprivileged actor capable of sending a `web_api_trigger` message through `HandleLegacyUserMessage` (this is the gateway's externally reachable legacy user-message path). Constructing a negative timestamp requires no special privilege beyond being able to sign an otherwise well-formed payload — the same capability needed to send a normal message. Because only `Timestamp == 0` is rejected and no bound (`>0`, `<= now`) is otherwise enforced, the wraparound is trivially reachable.

### Recommendation
Validate `payload.Timestamp` as a bounded, non-negative value before using it in the freshness comparison, and perform the comparison entirely in a signed/monotonic type to avoid wraparound, e.g.:
```go
if payload.Timestamp <= 0 {
    // reject
}
now := time.Now().Unix()
maxAge := int64(h.config.MaxAllowedMessageAgeSec)
if now-maxAge > payload.Timestamp {
    // stale message, reject
}
```
Avoid casting attacker-controlled `int64` values to `uint` for use in comparisons; keep timestamp arithmetic in signed 64-bit space, matching how `time.Now().Unix()` is already produced.

### Proof of Concept
1. A sender constructs a `webapicap.TriggerRequestPayload` with `Timestamp: -1` (or any negative int64) and a valid `trigger_id`/`topics`/`params`, then signs the enclosing `api.Message` as shown in `triggerRequest`/`gatewayRequest` test helpers [3](#0-2) [5](#0-4) .
2. Submit this message to the gateway's `HandleLegacyUserMessage` entry point.
3. In the freshness check `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)`, `uint(-1)` evaluates to `math.MaxUint`, so the left side (a small positive number) is never greater, and the "stale message" branch is never taken — the message is forwarded to all DON members regardless of its actual age or how many times it is replayed. [6](#0-5)

### Citations

**File:** core/capabilities/webapi/webapicap/event_trigger_generated.go (L101-117)
```go
type TriggerRequestPayload struct {
	// Key-value pairs for the workflow engine, untranslated.
	Params TriggerRequestPayloadParams `json:"params" yaml:"params" mapstructure:"params"`

	// Timestamp of the event (unix time), needs to be within certain freshness to be
	// processed.
	Timestamp int64 `json:"timestamp" yaml:"timestamp" mapstructure:"timestamp"`

	// Topics corresponds to the JSON schema field "topics".
	Topics []string `json:"topics" yaml:"topics" mapstructure:"topics"`

	// Uniquely identifies generated event (scoped to trigger_id and sender).
	TriggerEventId string `json:"trigger_event_id" yaml:"trigger_event_id" mapstructure:"trigger_event_id"`

	// ID of the trigger corresponding to the capability ID.
	TriggerId string `json:"trigger_id" yaml:"trigger_id" mapstructure:"trigger_id"`
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
```go
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
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L193-220)
```go
func triggerRequest(t *testing.T, key *ecdsa.PrivateKey, topics []string, methodName, timestamp, payload string) *api.Message {
	messageID := "12345"
	if methodName == "" {
		methodName = MethodWebAPITrigger
	}
	if timestamp == "" {
		timestamp = strconv.FormatInt(time.Now().Unix(), 10)
	}
	donID := "workflow_don_1"
	var payloadJSON []byte
	if payload == "" {
		ts, err := strconv.ParseInt(timestamp, 10, 64)
		require.NoError(t, err)
		reqPayload := webapicap.TriggerRequestPayload{
			TriggerId:      "web-api-trigger@1.0.0",
			TriggerEventId: "action_1234567890",
			Timestamp:      ts,
			Topics:         topics,
			Params: webapicap.TriggerRequestPayloadParams(map[string]any{
				"bid": "101",
				"ask": "102",
			}),
		}
		payloadJSON, err = json.Marshal(reqPayload)
		require.NoError(t, err)
	} else {
		payloadJSON = []byte(payload)
	}
```

**File:** core/capabilities/webapi/webapicap/event_trigger-schema.json (L64-68)
```json
                "timestamp": {
                    "type": "integer",
                    "format": "int64",
                    "description": "Timestamp of the event (unix time), needs to be within certain freshness to be processed."
                },
```

**File:** core/capabilities/webapi/trigger/trigger_test.go (L84-119)
```go
func gatewayRequest(t *testing.T, privateKey string, topics []string, methodName string) *jsonrpc.Request[json.RawMessage] {
	messageID := "12345"
	if methodName == "" {
		methodName = ghcapabilities.MethodWebAPITrigger
	}
	donID := "workflow_don_1"

	key, err := crypto.HexToECDSA(privateKey)
	require.NoError(t, err)

	payload := webapicap.TriggerRequestPayload{
		TriggerId:      TriggerType,
		TriggerEventId: "action_1234567890",
		Timestamp:      1234567890,
		Topics:         topics,
		Params: webapicap.TriggerRequestPayloadParams{
			"bid": "100",
			"ask": "101",
		},
	}

	payloadJSON, err := json.Marshal(payload)
	require.NoError(t, err)
	msg := &api.Message{
		Body: api.MessageBody{
			MessageId: messageID,
			Method:    methodName,
			DonId:     donID,
			Payload:   json.RawMessage(payloadJSON),
		},
	}
	err = msg.Sign(key)
	require.NoError(t, err)
	req, err := hc.ValidatedRequestFromMessage(msg)
	require.NoError(t, err)
	return req
```
