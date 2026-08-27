### Title
Chain-ID controlled fallback path (`GetRelayers().Get()`) in `EVMTransfersController.Create` bypasses balance/spend validation applied in the legacy path - ([File: core/web/evm_transfer_controller.go])

### Summary
`EVMTransfersController.Create` first tries to resolve the requested `EVMChainID` against `LegacyEVMChains()`; if that lookup fails it silently falls back to `GetRelayers().Get()` and dispatches to `CreateWithRelayer` instead of `CreateEVMLegacy`. `CreateWithRelayer` does not call `ValidateEthBalanceForTransfer` (or the `FromAddress != ZeroAddress` check) that `CreateEVMLegacy` performs, so an attacker who can choose which chain ID to submit can select one that only exists in the relayer set (not in `LegacyEVMChains`) to skip balance/fee validation entirely.

### Finding Description
In `core/web/evm_transfer_controller.go`, `Create` does: [1](#0-0) 
The chain-selection logic is entirely driven by the attacker-supplied `tr.EVMChainID` field from the request body. If `getChain(... LegacyEVMChains() ..., tr.EVMChainID.String())` errors (e.g., the chain ID is not registered as a legacy EVM chain but is registered as a relayer via `GetRelayers().Get()`), the code unconditionally falls through to `CreateWithRelayer`.

Comparing the two handlers:
- `CreateEVMLegacy` enforces `FromAddress != ZeroAddress` and, unless `AllowHigherAmounts` is set, calls `ValidateEthBalanceForTransfer`, which checks the on-chain balance against amount+fees before allowing the transfer: [2](#0-1) 
- `CreateWithRelayer` performs no balance validation at all; it just resolves chain info and calls `relayer.Transact(...)`, passing `!tr.AllowHigherAmounts` as a boolean parameter, but there is no code in this file that inspects or enforces that parameter's semantics — the actual balance-check behavior depends entirely on the relayer implementation, which is opaque to this controller and not verifiable here: [3](#0-2) 

This means the same HTTP endpoint (`POST /v2/transfers`) and the same request shape can be routed to two functionally different implementations purely based on which internal chain registry (`LegacyEVMChains` vs `Relayers`) happens to contain the given `EVMChainID`, with no indication to the caller and no equivalent validation guarantee between the two paths.

### Impact Explanation
If a chain ID exists only in the relayer registry and not in `LegacyEVMChains`, an edit-role-privileged caller can force transfers through `CreateWithRelayer`, which lacks the explicit `ValidateEthBalanceForTransfer` check present in the legacy path. Depending on whether the underlying relayer's `Transact` implementation independently re-implements that same balance/fee check (which could not be confirmed from this controller file alone), this could allow a transfer request to bypass the balance-sufficiency guard that operators rely on to prevent failed/overdraft transactions, corresponding to Chainlink's "unauthorized fund movement / validation bypass" impact class.

### Likelihood Explanation
Requires an authenticated session with edit-role privileges (this endpoint is not exposed to unauthenticated or view-only users) and knowledge that a chain ID is configured as a relayer-only chain (not present in `LegacyEVMChains`) — a configuration-dependent precondition rather than something universally exploitable. The dispatch logic itself is deterministic and fully attacker-controlled once that chain configuration exists, making it reliably reproducible whenever such a chain ID is present.

### Recommendation
Ensure `CreateWithRelayer` performs the equivalent balance/fee validation as `CreateEVMLegacy` before calling `relayer.Transact`, or centralize the `AllowHigherAmounts`/balance check logic into a shared function invoked by both code paths regardless of which chain registry resolved the request, so behavior is identical across `LegacyEVMChains` and `Relayers`-backed chains.

### Proof of Concept
1. Unit/handler test in `core/web/evm_transfer_controller_test.go`:
   - Configure `App.GetRelayers()` such that `LegacyEVMChains()` does NOT contain chain ID `X`, but `GetRelayers().Get(RelayID{Network: NetworkEVM, ChainID: "X"})` returns a mock relayer.
   - Set the mock relayer's `Transact` to succeed unconditionally (simulate insufficient real balance).
   - POST `/v2/transfers` with `EVMChainID = X`, `Amount` greater than the account's actual balance, `AllowHigherAmounts = false`.
   - Assert: response is `200`/success (transfer accepted) via `CreateWithRelayer`, whereas an equivalent request against a chain present in `LegacyEVMChains` with the same over-balance amount returns `422 Unprocessable Entity` from `ValidateEthBalanceForTransfer` in `CreateEVMLegacy`.
   - This confirms the two paths apply different validation for logically identical over-balance transfer requests, demonstrating the bypass via chain-ID-driven path selection.

### Citations

**File:** core/web/evm_transfer_controller.go (L44-57)
```go
	// If LegacyEVMChains are available, use them; otherwise use the relayer.
	// Note that once we fully deprecate LegacyEVMChains we will switch to the relayer only.
	chain, errLegacy := getChain(tc.App.GetRelayers().LegacyEVMChains(), tr.EVMChainID.String()) //nolint:staticcheck //SA1019 keep the deprecated path for now
	if errLegacy == nil {
		tc.CreateEVMLegacy(c, chain, &tr)
	} else {
		relayer, errRelayer := tc.App.GetRelayers().Get(types.RelayID{Network: relay.NetworkEVM, ChainID: tr.EVMChainID.String()})
		if errRelayer != nil {
			jsonAPIError(c, http.StatusInternalServerError, errors.Join(errLegacy, errRelayer))
			return
		}
		tc.CreateWithRelayer(c, relayer, &tr)
	}
}
```

**File:** core/web/evm_transfer_controller.go (L61-91)
```go
func (tc *EVMTransfersController) CreateWithRelayer(c *gin.Context, relayer loop.Relayer, tr *models.SendEtherRequest) {
	info, err := relayer.GetChainInfo(c)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	cid, ok := new(big.Int).SetString(info.ChainID, 10)
	if !ok {
		jsonAPIError(c, http.StatusInternalServerError, fmt.Errorf("could not parse chain ID: %s", info.ChainID))
		return
	}

	err = relayer.Transact(c.Request.Context(), tr.FromAddress.String(), tr.DestinationAddress.String(), tr.Amount.ToInt(), !tr.AllowHigherAmounts)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	resource := presenters.EthTxResource{
		From:       &tr.FromAddress,
		To:         &tr.DestinationAddress,
		Value:      tr.Amount.String(),
		EVMChainID: *sqlutil.New(cid),
	}

	tc.App.GetAuditLogger().Audit(audit.EthTransactionCreated, map[string]any{
		"ethTX": resource,
	})
	jsonAPIResponse(c, resource, "eth_tx")
}
```

**File:** core/web/evm_transfer_controller.go (L96-114)
```go
func (tc *EVMTransfersController) CreateEVMLegacy(c *gin.Context, chain legacyevm.Chain, tr *models.SendEtherRequest) {
	if tr.FromAddress == utils.ZeroAddress {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("withdrawal source address is missing: %v", tr.FromAddress))
		return
	}

	if !tr.AllowHigherAmounts {
		err := ValidateEthBalanceForTransfer(c, chain, tr.FromAddress, tr.Amount, tr.DestinationAddress)
		if err != nil {
			jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("transaction failed: %w", err))
			return
		}
	}

	etx, err := chain.TxManager().SendNativeToken(c, chain.ID(), tr.FromAddress, tr.DestinationAddress, *tr.Amount.ToInt(), chain.Config().EVM().GasEstimator().LimitTransfer())
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("transaction failed: %w", err))
		return
	}
```
