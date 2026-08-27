### Title
Vault gateway secrets circuit breaker blocks `DeleteSecrets`, preventing users from revoking compromised/expiring secrets - (File: core/services/gateway/handlers/vault/handler.go)

### Summary
`core/services/gateway/handlers/vault/handler.go` gates all three vault write methods — `handleSecretsCreate`, `handleSecretsUpdate`, and `handleSecretsDelete` — behind a single `writeMethodsEnabled.AllowErr(ctx)` circuit breaker. When this breaker is tripped (disabled), `DeleteSecrets` is rejected exactly the same way `CreateSecrets`/`UpdateSecrets` are, even though deletion is the user's self-protective/remediation action, analogous to `repay()` being blocked by `whenNotPaused` in the referenced report.

### Finding Description
The gateway-side vault handler defines three near-identical write handlers: [1](#0-0) [2](#0-1) [3](#0-2) 

Each of `handleSecretsCreate`, `handleSecretsUpdate`, and `handleSecretsDelete` performs the identical check:
```go
err := h.writeMethodsEnabled.AllowErr(ctx)
if errors.Is(err, limits.ErrorNotAllowed{}) {
    ...
    return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
}
```
Only `handleSecretsList` (a read-only path) is exempt: [4](#0-3) 

The `writeMethodsEnabled` limiter is a single circuit breaker/kill-switch that treats create, update, and delete as an undifferentiated group of "write methods." This mirrors the reported bug class: an emergency pause/circuit-breaker mechanism (`whenNotPaused` in OmniPool) is applied uniformly to all state-changing operations without carving out the self-protective/exit path (`repay()` in OmniPool; `DeleteSecrets` here).

An unprivileged workflow owner who needs to delete a leaked, compromised, or expiring secret (e.g., an API key that must be revoked immediately) cannot do so while an operator has disabled write methods via this breaker — the delete request is rejected with `UnsupportedMethodError` just like create/update, even though deletion reduces risk rather than introducing it.

### Impact Explanation
While write methods are disabled (e.g., during an incident response, a DKG resharing event, or an operational pause of the vault DON), a compromised or leaked secret cannot be deleted by its owner. The secret remains retrievable by the workflow capability (`GetSecrets`) for the duration of the pause, extending the exposure window of a known-compromised credential. This is analogous to the OmniPool case where the pause prevented the risk-reducing action (`repay`) rather than only the risk-increasing ones, causing avoidable harm to the unprivileged actor (secret owner) during exactly the window when remediation matters most.

### Likelihood Explanation
This requires the `writeMethodsEnabled` circuit breaker to be tripped/disabled, which is an operational/administrative condition rather than something an attacker can trigger directly. However, once tripped (which could occur during legitimate maintenance or an incident), every ordinary vault user attempting to revoke a leaked secret is affected — no privilege escalation or attacker action is needed to hit this path, only the coincidence of a compromised secret existing during a write-disabled window.

### Recommendation
Exempt `MethodSecretsDelete` from the `writeMethodsEnabled` circuit breaker (or provide a separate, narrower breaker for create/update only), so that users retain the ability to revoke/delete secrets even when new secret creation/updates are administratively disabled. This mirrors the BetaFinance fix of removing `whenNotPaused` from `repay()` while keeping it on other state-changing functions.

### Proof of Concept
1. Operator disables vault write methods via the `writeMethodsEnabled` limiter (e.g., during a security incident or maintenance window).
2. A workflow owner discovers their previously stored secret has leaked and needs to delete it immediately by sending a `MethodSecretsDelete` JSON-RPC request through the gateway.
3. `handleSecretsDelete` calls `h.writeMethodsEnabled.AllowErr(ctx)`, which returns `limits.ErrorNotAllowed{}` [5](#0-4) .
4. The handler responds with `api.UnsupportedMethodError` and the message `"vault write methods(create/update/delete) are disabled"`, refusing to delete the compromised secret.
5. The leaked secret remains active and retrievable via `GetSecrets` for the entire duration write methods stay disabled, unlike list/read operations which are unaffected.

Note: I was unable to locate the exact configuration source that flips `writeMethodsEnabled` (e.g., the specific limits config key/CLI flag) within the indexed portion of the codebase — only the `handler.go` consumption sites were found via search. If precise configuration wiring is needed, a full-repository review via a Devin session would be required to confirm operational trigger conditions.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L603-616)
```go
func (h *handler) handleSecretsCreate(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	err := h.writeMethodsEnabled.AllowErr(ctx)
	if errors.Is(err, limits.ErrorNotAllowed{}) {
		l.Warnw("secrets write method called but write methods are disabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
	} else if err != nil {
		l.Errorw("error checking if write methods are enabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("error checking if write methods are enabled: "+err.Error()), nil))
	}

	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L618-631)
```go
func (h *handler) handleSecretsUpdate(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	err := h.writeMethodsEnabled.AllowErr(ctx)
	if errors.Is(err, limits.ErrorNotAllowed{}) {
		l.Warnw("secrets write method called but write methods are disabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
	} else if err != nil {
		l.Errorw("error checking if write methods are enabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("error checking if write methods are enabled: "+err.Error()), nil))
	}

	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L633-646)
```go
func (h *handler) handleSecretsDelete(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	err := h.writeMethodsEnabled.AllowErr(ctx)
	if errors.Is(err, limits.ErrorNotAllowed{}) {
		l.Warnw("secrets write method called but write methods are disabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
	} else if err != nil {
		l.Errorw("error checking if write methods are enabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("error checking if write methods are enabled: "+err.Error()), nil))
	}

	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L648-651)
```go
func (h *handler) handleSecretsList(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)
	return h.fanOutToVaultNodes(ctx, l, ar)
}
```
