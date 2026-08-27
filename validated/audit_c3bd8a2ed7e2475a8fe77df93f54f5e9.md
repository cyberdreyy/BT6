### Title
Unauthenticated senders can fully exhaust the shared per-DON `RequestCache` and deny service to legitimate capability consumers - ([File: core/services/gateway/handlers/common/requestcache.go])

### Finding Description
`Message.Validate()` in `core/services/gateway/api/message.go` only checks field length bounds (`MessageIdMaxLen=128`, etc.) and that the signature is well-formed/recoverable via `ExtractSigner`; it performs no allowlist, subscription, or stake check on the recovered `m.Body.Sender` before setting it [1](#0-0) . Any party can generate an arbitrary ECDSA keypair, sign a message with `Sign`/`SignKS`, and produce a structurally valid `Message` with a unique `Sender`/`MessageId` pair for free [2](#0-1) .

`requestCache.NewRequest` keys pending requests by `globalId{sender, messageId}` and admits any new, distinct key as long as `len(c.cache) < maxCacheSize`, with no per-sender quota, subscription check, or authorization step ahead of the size check [3](#0-2) . Once `len(c.cache) >= maxCacheSize`, every subsequent `NewRequest` call — including from legitimate subscribed senders — fails with `"request cache is full"` [4](#0-3) .

However, I was unable to locate a production call site instantiating `NewRequestCache[T]` or invoking `requestCache.NewRequest` in the currently indexed handler code (`core/capabilities/vault/gw_handler.go`, `core/services/gateway/handlers/capabilities/handler.go`, and `core/services/gateway/handlers/capabilities/v2/http_handler.go` do not reference it) — the only matches found were in `requestcache_test.go`. The gateway's dispatch path (`gateway.go` `ProcessRequest`) routes validated messages into handler-specific logic (e.g., `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage`) whose concrete admission-control behavior for a given DON/service is defined per-handler [5](#0-4) . The capabilities handler that was inspected uses a different, eviction-based `savedCallbacks` map (bounded by `MaxSavedCallbacks`, oldest entries evicted rather than hard-rejected) rather than `RequestCache` [6](#0-5) . Because I could not confirm which live handler (if any) currently wires an attacker-reachable HTTP endpoint directly into `requestCache.NewRequest` without an intervening allowlist/authorization check (e.g., Vault's flow goes through `Authorizer`/`AllowListBasedAuth` before any per-request state is created, per `core/capabilities/vault/gw_handler.go`), this may reflect indexing limits rather than an absence of the vulnerable wiring elsewhere in the codebase.

### Impact Explanation
If a handler exists that calls `requestCache.NewRequest` directly on gateway messages without a preceding per-sender authorization/allowlist gate, an attacker could exhaust the shared cache and deny service to legitimate subscribed users for that DON — a DoS against capability execution, matching a resource-exhaustion/availability-impact bounty class. This cannot be confirmed as exploitable in the current index because no concrete unauthenticated call path into `requestCache.NewRequest` was found in the searched files, and the one handler pipeline observed (Vault) performs `Authorizer`/allowlist checks prior to any resource allocation [7](#0-6) .

### Likelihood Explanation
Low confidence given the lack of a confirmed production call site for `requestCache.NewRequest` reachable by an unauthenticated gateway client without prior authorization. Due to index size limits, some handler wiring may not be visible to this search; a full-repo scan (e.g., via a Devin session) would be needed to confirm whether any handler passes gateway-validated `api.Message`s directly into `RequestCache.NewRequest` without an allowlist/subscription check ahead of it.

### Recommendation
For any handler that does use `RequestCache`, ensure a per-sender/subscription authorization or allowlist check (like Vault's `Authorizer`) executes before `NewRequest`, and/or apply a per-sender quota within `requestCache` (not just a global `maxCacheSize`) so that no single sender (or set of freshly generated keys) can consume the entire shared cache.

### Proof of Concept
Could not be finalized: a concrete PoC requires identifying an actual attacker-reachable handler that calls `requestCache.NewRequest` without upstream authorization, which was not located in the available index. Recommend a background Devin session with full repository access to grep all handler implementations (`core/services/gateway/handlers/**`) for `NewRequestCache`/`.NewRequest(` usage and trace their message-admission order relative to authorization checks before concluding exploitability.

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

**File:** core/services/gateway/api/message.go (L96-122)
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

func (m *Message) SignKS(ctx context.Context, ks keys.MessageSigner, signer common.Address) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := ks.SignMessage(ctx, signer, gw_common.Flatten(rawData...))
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(signer.Hex())
	return nil
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-76)
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
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```

**File:** core/services/gateway/gateway.go (L250-273)
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

	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

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

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
	}
}
```

**File:** core/capabilities/vault/gw_handler.go (L180-212)
```go
func (h *GatewayHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) (err error) {
	reqLggr := h.requestLogger(req, gatewayID)
	reqLggr.Debugw("received message from gateway", "req", req)

	var response *jsonrpc.Response[json.RawMessage]
	var authResult *AuthResult

	switch req.Method {
	case vaulttypes.MethodSecretsCreate, vaulttypes.MethodSecretsUpdate:
		publicKey, pkErr := h.getMasterPublicKey(ctx)
		if pkErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pkErr)
			break
		}
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, publicKey)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}

```
