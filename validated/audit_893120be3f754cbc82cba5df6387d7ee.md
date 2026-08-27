### Title
Permanent, unrefreshed caching of the Vault master public key allows a DKG re-key to silently make user-submitted `SecretsCreate`/`SecretsUpdate` requests unrecoverable - (File: core/capabilities/vault/gw_handler.go)

### Summary
`GatewayHandler.getMasterPublicKey` caches the TDH2 master public key in `h.cachedMasterPublicKey` the first time it is fetched, and after that it is served from cache forever with no invalidation path. [1](#0-0)  If an administrator/DKG operator rotates the vault master key (a fresh DKG dealing, as opposed to a resharing that preserves the group key) via `ConfigureVaultDKG`, [2](#0-1)  the running node process keeps encrypting acceptance-side validation against — and returning — the old, stale public key to unprivileged clients calling `SecretsCreate`/`SecretsUpdate` through the gateway, since `getMasterPublicKey` is invoked unconditionally on those code paths. [3](#0-2)  This mirrors the report's underlying issue: users have no assurance about the current behavior of a function because a privileged operation (key rotation) changes system state without any warning, and their in-flight request silently starts operating on stale material.

### Finding Description
`GatewayHandler` is the node-side handler for Vault requests forwarded from the internet-facing gateway (unprivileged workflow clients send `SecretsCreate`/`SecretsUpdate` messages that arrive here). [4](#0-3) 

For `MethodSecretsCreate`/`MethodSecretsUpdate`, the handler fetches the master public key via `getMasterPublicKey` before processing the request: [3](#0-2) 

`getMasterPublicKey` implements an unconditional, never-expiring cache:
```go
func (h *GatewayHandler) getMasterPublicKey(ctx context.Context) (*tdh2easy.PublicKey, error) {
	h.mu.RLock()
	if h.cachedMasterPublicKey != nil {
		cachedCopy := *h.cachedMasterPublicKey
		h.mu.RUnlock()
		return &cachedCopy, nil
	}
	...
	h.cachedMasterPublicKey = publicKey
	...
}
``` [1](#0-0) 

There is no code path anywhere in this file (or in `gateway_vault_request_processor.go`) that clears or refreshes `cachedMasterPublicKey` once it is populated; the only way to obtain a fresh key is via `MasterPublicKeyFromSecretsService`, which is only reached on first use. [5](#0-4)  Meanwhile, DKG configuration tooling explicitly supports a "fresh dealing" that generates a brand-new master/group key (as opposed to resharing, which preserves the old key): [2](#0-1) 

If such a re-key happens after a node process has already cached the old key, that node will keep validating/serving the stale key for the lifetime of the process, while the underlying `secretsService` (and other nodes / the gateway's own `tryCachePublicKeyResponse` cache in `handlers/vault/handler.go`) may have moved to the new key. [6](#0-5)  This is directly analogous to the report's `SetZauction` issue: a privileged/administrative action (key rotation) changes system behavior for an in-flight or subsequent client request with no notice, no time lock, and no mechanism for the client to know the interaction they are about to perform (encrypting a secret) is based on state that is about to become (or already is) invalid.

### Impact Explanation
Clients (workflow owners) use the returned/cached master public key to build `CreateSecretsRequest`/`UpdateSecretsRequest` payloads (see `PrepareSecrets` encrypting against `vaultPublicKey`). [7](#0-6)  If a node serves (or accepts under) a stale cached master public key after a genuine DKG re-key, requests encrypted under that stale key become undecryptable by the new DKG share holders, causing silent failure or loss of secret material for legitimate, unprivileged clients — a functional/availability impact analogous to the "seller can no longer accept bids" scenario in the report, and in a mixed-node fleet it also produces cross-node/cross-client inconsistency (some nodes return the old key, others the new one) for the same logical request.

### Likelihood Explanation
This requires a legitimate administrative DKG re-key event to occur while a `GatewayHandler` process is already running and has already cached a key — a plausible operational event (key rotation/incident response), not an attacker-controlled trigger. The likelihood of the specific negative outcome (client encrypts against a now-void key) depends on operational timing around re-key events, similar to the original report's framing of "unfortunate timing" rather than attacker-only exploitation.

### Recommendation
- Do not cache the master public key indefinitely; add a TTL/expiry or an explicit invalidation hook that is triggered when the underlying `secretsService`/DKG instance changes.
- Alternatively, always re-fetch and validate `GetPublicKey` against the currently active DKG instance ID/config digest, similar to `VerifyDKGResult`'s comparison of the reported key against the on-chain/expected value. [8](#0-7) 
- Version the master public key (tie it to the DKG `InstanceID`/config digest) and reject or explicitly flag `SecretsCreate`/`SecretsUpdate` requests encrypted under a key whose instance ID no longer matches the currently active one, rather than silently accepting/serving stale key material.

### Proof of Concept
Conceptual (no PoC executed, based on code inspection):
1. Start a `GatewayHandler` node; a client submits `SecretsList`/`SecretsCreate`, which calls `getMasterPublicKey`, caching `PublicKey_v1` in `h.cachedMasterPublicKey`. [1](#0-0) 
2. Operator runs a fresh DKG dealing (not resharing) via `ConfigureVaultDKG`, producing a new group key `PublicKey_v2`, and the underlying `secretsService` now reports `PublicKey_v2` from `GetPublicKey`. [2](#0-1) 
3. Any subsequent `SecretsCreate`/`SecretsUpdate` handled by the still-running `GatewayHandler` process continues to return/use the stale `PublicKey_v1` from cache instead of `PublicKey_v2`, because `getMasterPublicKey` short-circuits on the non-nil cache before ever calling `MasterPublicKeyFromSecretsService` again. [9](#0-8) 
4. A workflow owner who fetches `PublicKey_v1` from this node and encrypts a secret against it submits `CreateSecretsRequest`; the secret is stored encrypted under a key for which the correct new share holders can no longer produce a matching decryption, causing silent loss/failure of the secret with no warning to the client at request time.

### Citations

**File:** core/capabilities/vault/gw_handler.go (L180-199)
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
```

**File:** core/capabilities/vault/gw_handler.go (L238-261)
```go
func (h *GatewayHandler) getMasterPublicKey(ctx context.Context) (*tdh2easy.PublicKey, error) {
	h.mu.RLock()
	if h.cachedMasterPublicKey != nil {
		cachedCopy := *h.cachedMasterPublicKey
		h.mu.RUnlock()
		return &cachedCopy, nil
	}
	h.mu.RUnlock()

	publicKey, err := MasterPublicKeyFromSecretsService(ctx, h.secretsService)
	if err != nil {
		return nil, err
	}

	h.mu.Lock()
	defer h.mu.Unlock()
	if h.cachedMasterPublicKey != nil {
		cachedCopy := *h.cachedMasterPublicKey
		return &cachedCopy, nil
	}
	h.cachedMasterPublicKey = publicKey
	cachedCopy := *publicKey
	return &cachedCopy, nil
}
```

**File:** deployment/cre/ocr3/ocr3_1/changeset/operations/contracts/configure_vault_dkg.go (L49-58)
```go
	// RecipientPublicKeys are the DKG public keys of the recipients (the resulting participant
	// set that will hold shares of the master secret). NodeIDs correspond positionally to
	// RecipientPublicKeys.
	RecipientPublicKeys []string `json:"recipientPublicKeys" yaml:"recipientPublicKeys"`
	// PreviousInstanceID, when set, makes this a resharing DKG instead of a fresh dealing.
	// It must be the currently-live DKG instance ID (e.g.
	// "sanmarinodkg/v1/<dkgContract>/<configDigest>"). Resharing preserves the group
	// (master) public key while changing the participant/share set; a fresh dealing
	// (nil) generates a NEW group key. Leave nil only for the very first DKG config.
	PreviousInstanceID *string `json:"previousInstanceID,omitempty" yaml:"previousInstanceID,omitempty"`
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L292-312)
```go
// MasterPublicKeyFromSecretsService loads the vault master public key from a secrets service.
func MasterPublicKeyFromSecretsService(ctx context.Context, secretsService vaulttypes.SecretsService) (*tdh2easy.PublicKey, error) {
	resp, err := secretsService.GetPublicKey(ctx, &vaultcommon.GetPublicKeyRequest{})
	if err != nil {
		return nil, fmt.Errorf("failed to get vault public key: %w", err)
	}
	if resp == nil || resp.PublicKey == "" {
		return nil, errors.New("vault public key is unavailable")
	}

	masterPublicKeyBytes, err := hex.DecodeString(resp.PublicKey)
	if err != nil {
		return nil, fmt.Errorf("failed to decode vault public key: %w", err)
	}

	masterPublicKey := &tdh2easy.PublicKey{}
	if err := masterPublicKey.Unmarshal(masterPublicKeyBytes); err != nil {
		return nil, fmt.Errorf("failed to unmarshal vault public key: %w", err)
	}
	return masterPublicKey, nil
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L539-573)
```go
func (h *handler) tryCachePublicKeyResponse(resp *jsonrpc.Response[json.RawMessage], l logger.Logger) {
	if resp.Result == nil {
		l.Debugw("no result in public key response, not caching")
		return
	}

	r := &vaultcommon.GetPublicKeyResponse{}
	err := h.unmarshal(bytes.NewReader(*resp.Result), r)
	if err != nil {
		l.Debugw("failed to unmarshal public key response, not caching", "error", err)
		return
	}

	if r.PublicKey == "" {
		l.Debugw("no public key in unmarshaled response, not caching", "response", resp, "result", r)
		return
	}
	masterPublicKey := tdh2easy.PublicKey{}
	masterPublicKeyBytes, err := hex.DecodeString(r.PublicKey)
	if err != nil {
		l.Debugw("failed to decode master public key string", "error", err)
		return
	}
	err = masterPublicKey.Unmarshal(masterPublicKeyBytes)
	if err != nil {
		l.Debugw("failed to unmarshal master public key", "error", err)
		return
	}

	h.mu.Lock()
	h.cachedPublicKeyGetResponse = *resp.Result
	h.cachedPublicKeyObject = &masterPublicKey
	h.mu.Unlock()
	l.Debugw("successfully cached public key response")
}
```

**File:** system-tests/lib/cre/workflow/secrets.go (L97-121)
```go
	masterPublicKeyBytes, err := hex.DecodeString(vaultPublicKey)
	if err != nil {
		return "", errors.Wrap(err, "failed to decode vault public key")
	}
	masterPublicKey := &tdh2easy.PublicKey{}
	if err := masterPublicKey.Unmarshal(masterPublicKeyBytes); err != nil {
		return "", errors.Wrap(err, "failed to unmarshal vault public key")
	}

	encryptedSecrets := make([]*vault_helpers.EncryptedSecret, 0, len(cfg.Secrets))
	for _, entry := range cfg.Secrets {
		value := os.Getenv(entry.EnvVar)
		if value == "" {
			return "", fmt.Errorf("environment variable %q is not set for secret key %q", entry.EnvVar, entry.Key)
		}

		namespace := entry.Namespace
		if namespace == "" {
			namespace = "main"
		}

		encryptedValue, encErr := vaultutils.EncryptSecretWithWorkflowOwner(value, masterPublicKey, ownerAddress)
		if encErr != nil {
			return "", errors.Wrapf(encErr, "failed to encrypt secret %q", entry.Key)
		}
```

**File:** core/capabilities/vault/verify.go (L13-37)
```go
func VerifyDKGResult(resultPackage []byte, masterPublicKey string, key dkgocrtypes.P256Keyring) error {
	rp := dkgocr.NewResultPackage()
	err := rp.UnmarshalBinary(resultPackage)
	if err != nil {
		return fmt.Errorf("could not unmarshal result package: %w", err)
	}

	tdh2PubKey, err := tdh2shim.TDH2PublicKeyFromDKGResult(rp)
	if err != nil {
		return fmt.Errorf("could not derive TDH2 public key from DKG result: %w", err)
	}

	pubKeyBytes, err := tdh2PubKey.Marshal()
	if err != nil {
		return fmt.Errorf("could not marshal TDH2 public key: %w", err)
	}

	mpk, err := hex.DecodeString(masterPublicKey)
	if err != nil {
		return fmt.Errorf("could not hex decode master public key from request: %w", err)
	}

	if !bytes.Equal(pubKeyBytes, mpk) {
		return fmt.Errorf("master public key does not match: got %x, want %x", pubKeyBytes, mpk)
	}
```
