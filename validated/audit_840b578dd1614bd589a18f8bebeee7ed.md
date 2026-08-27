### Title
Missing allowlist/rate-limiting before DON-wide fan-out enables unauthenticated amplification DoS - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` only validates message signature well-formedness, payload decodability, and message staleness before fanning a `MethodWebAPITrigger` request out to every `h.donConfig.Members` node via `don.SendToNode`. The `// TODO: apply allowlist and rate-limiting here` comment confirms no allowlist or per-sender rate limit is applied at this layer, so any holder of an arbitrary ECDSA key can generate valid signed messages and force fan-out to all DON nodes.

### Finding Description
The request path is: gateway HTTP endpoint → `gateway.ProcessRequest` (core/services/gateway/gateway.go:218-292) → `msg.Validate()` (core/services/gateway/api/message.go:54-88), which only checks field-length constraints and signature well-formedness/recoverability (any valid ECDSA signature over the message, from any key, passes) → `h.HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421).

Inside `HandleLegacyUserMessage`, the checks performed prior to fan-out are: payload JSON decode, `payload.Timestamp == 0`, and staleness (`time.Now - MaxAllowedMessageAgeSec > payload.Timestamp`) — none of these constrain *who* the sender is. Immediately after the staleness check sits the TODO comment (line 384) acknowledging the absence of allowlist/rate-limiting, followed only by a method check. There is no check of `msg.Body.Sender` against any allowlist, and `HandlerConfig` (lines 63-70) contains no per-user-sender rate limiter (only `nodeRateLimiter`, which throttles DON→Gateway outgoing messages in `handleWebAPIOutgoingMessage`, not incoming user messages). `config.DONConfig` (core/services/gateway/config/config.go:34-41) likewise has no allowed-senders list. As a result, any attacker who signs a message with a fresh, arbitrary ECDSA key satisfies `Validate()` and reaches the fan-out loop at lines 417-419, which calls `don.SendToNode` once per DON member for every request.

The existing test suite includes an explicit acknowledgment of this gap: `core/services/gateway/handlers/capabilities/handler_test.go:365` — `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated`.

### Impact Explanation
Because no allowlist or rate limiting exists at the point of fan-out, an unauthenticated caller (anyone able to reach the gateway's user-facing HTTP endpoint and generate an ECDSA signature — no credential, registration, or prior relationship required) can submit unlimited distinct, validly-timestamped `MethodWebAPITrigger` messages with distinct `MessageId`/sender keys. Each request causes one `don.SendToNode` call per DON member, so request volume is amplified by the DON member count against every node backing that DON. This matches a denial-of-service / resource-exhaustion impact class against DON nodes reachable through the gateway. It also creates attribution ambiguity: since no sender is checked against an allowlist, illegitimate flood traffic is indistinguishable from legitimate, and any future coarse rate limiting keyed on sender identity could be trivially evaded by rotating keys, masking the true source of load.

### Likelihood Explanation
Preconditions are minimal: no credential, registration, or prior authorization is required — only the ability to generate an ECDSA keypair and sign a JSON-RPC-style message locally (`msg.Sign`), which is standard client-side code already present in the codebase (`api.Message.Sign`). The attack is fully repeatable and requires no coordination with DON members or gateway operators, and no rate limiting throttles distinct senders, so volume is bounded only by attacker resources and gateway-level infrastructure (e.g., load balancer) limits, which are outside this code's scope.

### Recommendation
Implement the allowlist/rate-limiting called out by the TODO before the fan-out loop in `HandleLegacyUserMessage`:
1. Validate `msg.Body.Sender` against a configured allowlist (e.g., extend `config.DONConfig` with an `AllowedSenders`/workflow-owner list) and reject unknown senders with a clear error response before any `don.SendToNode` call.
2. Add a per-sender rate limiter (analogous to `h.nodeRateLimiter`, e.g., `ratelimit.NewRateLimiter` keyed by `msg.Body.Sender`) enforced immediately after signature/staleness validation and before the fan-out loop.
3. Ensure rejected requests do not consume `savedCallbacks` slots or trigger `don.SendToNode`.

### Proof of Concept
Go test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Reuse `setupHandler` and `triggerRequest` helpers.
2. In a loop (e.g., N=1000), generate a fresh `ecdsa.PrivateKey`, build a unique `MessageId` and valid timestamp via `triggerRequest(t, freshKey, topics, "", "", "")`.
3. Assert on the mock `don` (`don.On("SendToNode", ...)`) that `SendToNode` is called once per `h.donConfig.Members` for every iteration, with no allowlist rejection ever occurring — i.e., `don.AssertNumberOfCalls(t, "SendToNode", N*len(members))`.
4. Add a companion "expected" test asserting that after introducing an allowlist, requests from senders not in the allowlist produce a `handlers.UserCallbackPayload` with an error code (e.g., a new `SenderNotAllowedError`) and `don.SendToNode` is never invoked for those senders — demonstrating the fix trips before fan-out, contrasting with the current unconditional forwarding behavior confirmed by the "happy case" fan-out test at handler_test.go:242-265.