This report describes a pattern specific to Tokensoft's `Distributor`/`AdvancedDistributor`: an admin `adjust` function that can decrease a beneficiary's `total` (returning tokens to owner) but never increases the contract's token balance on increase, so a later `settle`/`claim` transfer can revert due to insufficient balance, permanently freezing other users' claims.

The Aera `ProvisionerV2` code does not have an analogous "admin adjusts total without moving tokens" mechanism. All token movements I reviewed are tightly coupled 1:1 transfers within the same transaction:

- `deposit`/`mint` pull tokens in and mint units atomically via `_syncDeposit`, with no separate "total owed" ledger that can be adjusted independently of actual token custody.
- `refundDeposit` calls `_pullFundsIfNeeded` before transferring, explicitly pulling funds back from the yield source if the vault's idle balance is insufficient, precisely to avoid the "not enough tokens" failure mode the report describes. [1](#0-0) 
- `solveRequestsDirect`/`_solveRequestDirect` and the async solve paths (`_solveDepositVaultAutoPrice`, `_solveRedeemVaultAutoPrice`, `_solveRedeemVaultFixedPrice`) transfer tokens/units in the same call that solves the request; there's no separate "adjust total then settle later" step that could desync escrowed accounting from actual balances. [2](#0-1) 
- `cancelRequest`/`refundRequest` clear the request hash and transfer exactly the escrowed `request.tokens`/`request.units` amount, which were escrowed atomically at request time — no owner-adjustable "total" ledger disconnected from token custody exists here either.
<invoke name="grep_search">
<parameter name="pattern">function adjust|totalClaimed|totalOwed|owedAmount</parameter>
<parameter name="repo_name">ThankGod76/aera-contracts-public--020</parameter>
</invoke>

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L249-256)
```text
        // Interactions: pull funds from yield source if vault idle balance is insufficient
        _pullFundsIfNeeded(token, tokenAmount);

        // Interactions: exit vault, fallback to sender
        try IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, receiver) { }
        catch {
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, sender);
        }
```

**File:** v3/src/core/ProvisionerV2.sol (L1312-1339)
```text
    function _solveRequestDirect(IERC20 token, RequestV2 calldata request) internal {
        bytes32 requestHash = _getRequestHash(token, request);
        // Requirements: hash has been set
        if (_guardInvalidRequestHash(requestHash)) return;

        // Effects: unset hash as used
        asyncRequestHashes[requestHash] = false;

        bool isDeposit = _isRequestTypeDeposit(request.requestType);

        if (request.deadline >= block.timestamp) {
            if (isDeposit) {
                // Interactions: pull units from sender(solver) to receiver
                IERC20(MULTI_DEPOSITOR_VAULT).safeTransferFrom(msg.sender, request.receiver, request.units);
                // Interactions: transfer tokens from provisioner to sender
                token.safeTransfer(msg.sender, request.tokens);

                // Log solved event
                emit DepositSolved(requestHash);
            } else {
                // Interactions: transfer units from provisioner to sender
                IERC20(MULTI_DEPOSITOR_VAULT).safeTransfer(msg.sender, request.units);
                // Interactions: pull tokens from sender(solver) to receiver
                token.safeTransferFrom(msg.sender, request.receiver, request.tokens);

                // Log solved event
                emit RedeemSolved(requestHash);
            }
```
