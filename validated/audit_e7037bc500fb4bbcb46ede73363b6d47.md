### Title
Legacy gateway user-message path skips allowlist/rate-limiting enforced on the JSON-RPC path - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The Pear report's root cause is a code path that returns funds through an alternate mechanism (native ETH transfer) which the fee-collection function (`withdrawClosedSize`) never accounts for, letting an unprivileged actor route around a control that's only enforced on the "normal" path. The gateway's `capabilities` handler has the same structural flaw: incoming user requests are dispatched via two different code paths — a "new" JSON-RPC path (enforces authorization/allowlisting, e.g. as implemented for the vault service) and a "legacy" path (`HandleLegacyUserMessage`) that explicitly skips the allowlist/rate-limit check that protects the DON from being flooded/spammed by unauthorized senders.

### Finding Description
The internet-facing gateway HTTP server accepts arbitrary unauthenticated requests on the user port [1](#0-0) , and dispatches them via `gateway.ProcessRequest`, which branches into two routing modes depending on whether the request carries a `DonId` (legacy format) or not (new format): [2](#0-1) 

For the legacy branch, the capabilities handler's `HandleLegacyUserMessage` is invoked directly. Inside that function there is an explicit admission that the allowlist and rate-limiting logic that protects this endpoint from unauthorized/abusive senders has not been implemented on this path: [3](#0-2) 

By contrast, the newer JSON-RPC-routed handlers for other services (e.g. the vault gateway handler) go through an `Authorizer`/allowlist chain before dispatching to the DON: [4](#0-3) 

Since any unauthenticated client can send a "legacy" formatted request (one with `Body.DonId` populated) directly to the gateway's user-facing HTTP endpoint, they can reach `HandleLegacyUserMessage` and have their request forwarded to every member node of the DON [5](#0-4)  without ever being checked against an allowlist of authorized senders or a per-sender rate limit — the protections that exist to keep this internet-facing surface from being abused by arbitrary requesters.

### Impact Explanation
An unprivileged/unauthenticated client can bypass allowlist and rate-limiting protections simply by using the legacy request format, causing arbitrary requests to be broadcast to every node in the DON. This is analogous to a quota/allowlist bypass: the gateway's DON-facing trust boundary is meant to restrict which senders can trigger job execution/dispatch requests to nodes, but the alternate ("legacy") request path completely omits that check, as acknowledged by the inline TODO.

### Likelihood Explanation
The legacy branch is reachable directly from the public gateway HTTP endpoint by any external caller who forms a request with a non-empty `DonId` in the body — no credentials, JWT, or allowlist membership required to reach the handler. The only structural gate (`msg.Validate()`) checks message well-formedness, not sender authorization [6](#0-5) .

### Recommendation
Apply the same allowlist/rate-limiting authorization chain used on the JSON-RPC path (as implemented in `core/capabilities/vault/gw_handler.go`'s `Authorizer`) to `HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` before forwarding messages to DON nodes, removing the outstanding TODO.

### Proof of Concept
Not independently verifiable without live infrastructure; the code-level evidence is the explicit `// TODO: apply allowlist and rate-limiting here` comment directly preceding the method-validation and DON-broadcast logic in `HandleLegacyUserMessage`, contrasted with the enforced `Authorizer.AuthorizeRequest` call path used for JSON-RPC-routed services such as vault.

### Citations

**File:** core/services/gateway/network/httpserver.go (L180-219)
```go
func (s *httpServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.config.CORSEnabled {
		origin := r.Header.Get("Origin")
		if s.isAllowedOrigin(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}

		// handle preflight requests
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
	}

	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		msg := "Failed to get request size limit"
		s.lggr.Errorw(msg, "err", err)
		http.Error(w, msg, http.StatusInternalServerError)
		return
	}
	source := http.MaxBytesReader(nil, r.Body, int64(maxRequestBytes))
	rawMessage, err := io.ReadAll(source)
	if err != nil {
		s.lggr.Error("error reading request", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}

	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
```

**File:** core/services/gateway/gateway.go (L232-273)
```go
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L382-396)
```go
		})
	}
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

**File:** core/capabilities/vault/gw_handler.go (L84-126)
```go
func NewGatewayHandler(
	secretsService vaulttypes.SecretsService,
	connector gatewayConnector,
	workflowRegistrySyncer workflowsyncerv2.WorkflowRegistrySyncer,
	lggr logger.Logger,
	limitsFactory limits.Factory,
	authorizer Authorizer,
	auth0 *Auth0Config,
) (*GatewayHandler, error) {
	var jwtAuthService services.Service
	var jwtBasedAuth Authorizer
	if auth0 != nil {
		var err error
		jwtAuthService, err = NewJWTBasedAuth(JWTBasedAuthConfig{
			IssuerURL: auth0.IssuerURL,
			Audience:  auth0.Audience,
			TenantID:  auth0.TenantID,
		}, limitsFactory, lggr)
		if err != nil {
			return nil, fmt.Errorf("failed to create JWTBasedAuth: %w", err)
		}
		jwtBasedAuth = jwtAuthService.(Authorizer)
	}

	if authorizer == nil {
		allowListBasedAuth := NewAllowListBasedAuth(lggr, workflowRegistrySyncer)
		authorizer = NewAuthorizer(allowListBasedAuth, jwtBasedAuth, lggr)
	}

	requestValidator, err := NewRequestValidatorFromLimitsFactory(limitsFactory)
	if err != nil {
		return nil, fmt.Errorf("failed to create request validator: %w", err)
	}

	metrics, err := newMetrics()
	if err != nil {
		return nil, fmt.Errorf("failed to create metrics: %w", err)
	}

	requestProcessor, err := NewGatewayVaultRequestProcessor(requestValidator, authorizer, true, lggr)
	if err != nil {
		return nil, fmt.Errorf("failed to create gateway vault request processor: %w", err)
	}
```
