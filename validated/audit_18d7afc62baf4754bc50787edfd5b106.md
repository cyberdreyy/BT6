I have sufficient information to answer definitively based on the confirmed code in `handler.go`.

### Title
Read-path (`handleSecretsList`) bypasses `writeMethodsEnabled` gate enforced on create/update/delete, allowing secret-identifier enumeration during a mutation lockdown - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`handleSecretsCreate`, `handleSecretsUpdate`, and `handleSecretsDelete` all check `h.writeMethodsEnabled.AllowErr(ctx)` before forwarding a request to vault nodes, but `handleSecretsList` performs no equivalent gate check and unconditionally calls `h.fanOutToVaultNodes`. An authenticated/authorized owner who is blocked from mutating operations by the `GatewayVaultManagementEnabled` gate can still call `vault.secrets.list` and enumerate their own secret identifiers (namespace/key names, not values) unimpeded.

### Finding Description
In `core/services/gateway/handlers/vault/handler.go`, `handleSecretsCreate` (line 603), `handleSecretsUpdate` (line 618), and `handleSecretsDelete` (line 633) each begin with: [1](#0-0) 
which calls `h.writeMethodsEnabled.AllowErr(ctx)` and short-circuits with `api.UnsupportedMethodError` if the `GatewayVaultManagementEnabled` gate (`cresettings.Default.GatewayVaultManagementEnabled`, wired at line 237) denies the request.

`handleSecretsList`, however, is: [2](#0-1) 
which contains no analogous gate check and forwards directly to `fanOutToVaultNodes`. The dispatch in `HandleJSONRPCUserMessage` (lines 452-463) routes `vaulttypes.MethodSecretsList` to `handleSecretsList` after only the generic `requestProcessor.ProcessRequest` authorization/quorum check — the same authorization step used for the other three methods — meaning the asymmetry is specific to the `writeMethodsEnabled` gate and not a difference in authentication rigor.

Downstream, `ListSecretIdentifiers` is owner-scoped (`core/capabilities/vault/capability.go`, lines 214-222) and the OCR plugin (`processListSecretIdentifiersRequest`, `core/services/ocr2/plugins/vault/plugin.go` lines 1332-1371) returns only `SecretIdentifier{Owner, Namespace, Key}` metadata for the requesting owner — not decrypted secret values — but that metadata is exactly what an operator would want blocked during an incident-driven lockdown of `GatewayVaultManagementEnabled`.

### Impact Explanation
This falls under a limited "authorization/allowlist gating inconsistency" — an operator flipping the `GatewayVaultManagementEnabled` gate off (e.g., during an incident, suspected compromise, or planned freeze of vault mutations) expects all sensitive vault operations to be restricted, but list/read access remains fully available for any owner whose auth is otherwise valid. This is an information-disclosure/enumeration gap (secret identifiers, namespaces, and key names for an owner) rather than secret-value exposure, and does not itself grant privilege escalation or fund movement.

### Likelihood Explanation
The precondition set is narrow but realistic: the attacker needs the same valid/authorized owner credential that would already be required to call create/update/delete (allow-list or JWT-based `Authorizer`), and the operator must have disabled `writeMethodsEnabled` via the `GatewayVaultManagementEnabled` setting. Given valid auth for an owner, invoking `vault.secrets.list` is trivial and repeatable — no rate limiting or additional gate is involved beyond the generic node rate limiter (`h.nodeRateLimiter`) and request-processor authorization, which don't address the mutation-lockdown intent at all.

### Recommendation
Add a read-gate check (or reuse `writeMethodsEnabled`, or introduce a dedicated `GatewayVaultReadEnabled`/`GatewayVaultManagementEnabled`-covers-list gate) at the top of `handleSecretsList`, mirroring the pattern in `handleSecretsCreate`/`handleSecretsUpdate`/`handleSecretsDelete`, so that operators can consistently lock down all four vault methods (or explicitly document/scope the gate as "write-only by design" if that is the intended behavior).

### Proof of Concept
Table-driven Go test in `core/services/gateway/handlers/vault` (new or extending existing handler tests):
1. Construct a `handler` via `newHandlerWithAuthorizer` with a `limitsFactory` returning a `writeMethodsEnabled` gate set to `false` (disallow), using `limits.NewGateLimiter(false)` semantics as seen in `plugin_helpers_test.go`.
2. For each of `MethodSecretsCreate`, `MethodSecretsUpdate`, `MethodSecretsDelete`, `MethodSecretsList`, invoke `HandleJSONRPCUserMessage` with a valid authorized request for an owner.
3. Assert: create/update/delete responses return `api.UnsupportedMethodError` (write methods disabled) without reaching `fanOutToVaultNodes`/`don.SendToNode`.
4. Assert: the list request instead proceeds to `fanOutToVaultNodes` (verify via a mock `gwhandlers.DON.SendToNode` being invoked), confirming no gate blocks it — demonstrating the asymmetry documented above.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L606-613)
```go
	err := h.writeMethodsEnabled.AllowErr(ctx)
	if errors.Is(err, limits.ErrorNotAllowed{}) {
		l.Warnw("secrets write method called but write methods are disabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
	} else if err != nil {
		l.Errorw("error checking if write methods are enabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("error checking if write methods are enabled: "+err.Error()), nil))
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L648-651)
```go
func (h *handler) handleSecretsList(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)
	return h.fanOutToVaultNodes(ctx, l, ar)
}
```
