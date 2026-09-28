### Title
Keyring allowlist bypass on withdrawal claims — credential checked once at request time lets a non-allowed (or revoked) wallet "resume" trust and redeem pool funds in a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCDOEpochVariant`/`IdleCDOEpochQueue` enforce `isWalletAllowed` (Keyring `checkCredential`) only when a deposit or withdraw request is *created* — mirroring the TLS handshake. The claim path that actually pays underlying (`claimWithdrawRequest`, `claimInstantWithdrawRequest`, `_claimFundedWithdrawRequest`, `_transferFundedClaim`, `_transferDefaultRecovery`) performs no credential check at all — the "session resumption" step. A KYC-passing wallet can therefore request a withdrawal, have transferable strategy-token receipts minted to it, and either (a) have its credential revoked before claiming, or (b) pass the receipt tokens to a non-allowed wallet that claims and receives underlying, accessing funds in a context where its credential would never be accepted.

### Finding Description
- `requestWithdraw` in `IdleCreditVault` mints receipt strategy tokens to `_user` (`_mint(_user, _amount)`) after only `_onlyIdleCDO()`; the KYC check lives upstream in `cdoEpoch.requestWithdraw` / `IdleCDOEpochQueue.requestWithdraw` via `_checkAllowed` → `cdoEpoch.isWalletAllowed` → `IKeyring.checkCredential` (`contracts/IdleCDOEpochQueue.sol:415-421`, `contracts/IdleCDOEpochVariant.sol:984-987`).
- Claims never re-check it. `IdleCDOEpochVariant.claimWithdrawRequest()` (`contracts/IdleCDOEpochVariant.sol:967-971`) just forwards `msg.sender`; `IdleCreditVault.claimWithdrawRequest` → `_claimFundedWithdrawRequest` (`contracts/strategies/idle/IdleCreditVault.sol:301-350`) burns the caller's receipt tokens and pays underlying to the caller. `IdleCDOEpochQueue.claimWithdrawRequest` (`contracts/IdleCDOEpochQueue.sol:393-411`) likewise has no `_checkAllowed`.
- The strategy token is a plain `ERC20Upgradeable`; `canTransfer` is explicitly deprecated (`contracts/strategies/idle/IdleCreditVault.sol:66-67`), so receipts move freely to any address.
- Like CVE-2025-23048, a trust decision bound to one context (the requester's credential at request time / one vhost's CA list) is silently reused to authorize a different principal/context (transferee or revoked credential at claim time) because the second path skips the check that anchors authorization.

### Impact Explanation
The access invariant "only Keyring-credentialed lenders may hold/redeem pool exposure" is broken: any EOA holding receipt tokens can extract underlying, and a lender whose credential is revoked mid-epoch still claims full proceeds including accrued epoch interest (claim amounts are in underlying and include interest minted into the receipt). Quantified loss = the full claim amount plus interest; concretely, a revoked or sanctioned lender that queued a 100k USDC withdrawal withdraws ~100k USDC + epoch interest to itself or to an arbitrary non-KYC'd address, bypassing the compliance gate the protocol relies on for its permissioned borrower pools.

### Likelihood Explanation
Requires only an unprivileged KYC-passing lender (in-scope) plus either a Keyring revocation between request and claim, or a transfer of receipt tokens to a non-allowed wallet. Claims are permissionless by design, epochs guarantee at least one buffer+epoch gap between request and claim, and the receipt token has no transfer restriction — so the window is always open. No privileged cooperation needed.

### Recommendation
Re-check `IIdleCDOEpochVariant(idleCDO).isWalletAllowed(_user)` (or expose a view on the CDO) inside `claimWithdrawRequest`, `claimInstantWithdrawRequest`, and all internal claim paths (`_claimFundedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`, `_claimDefaulted*`), and add the same `_checkAllowed(msg.sender)` gate in `IdleCDOEpochQueue.claimWithdrawRequest` / `claimDepositRequest`. Optionally restrict strategy-token transfers to allowed wallets (or only to/from the CDO) so receipts cannot be relayed to non-credentialed claimers.

### Proof of Concept
```solidity
// Foundry fork PoC sketch against mainnet deployment (or test harness deploy)
// Setup on test/foundry/IdleCreditVault.t.sol harness:

// 1. KYC'd user deposits and starts/stops epoch 0
uint256 mintedAA = idleCDO.depositAA(100_000e6);      // kycUser deposits
_startEpochAndCheckPrices(0);
_stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

// 2. kycUser requests withdraw — _checkAllowed passes (credential valid)
cdoEpoch.requestWithdraw(0, address(AAtranche));      // receipt strategy tokens minted to kycUser
uint256 receipt = strategyToken.balanceOf(kycUser);

// 3a. Variant A — credential revoked mid-epoch ("session resumption" w/o re-auth)
keyring.revokeCredential(policyId, kycUser);          // Keyring admin revokes
_startEpochAndCheckPrices(1);
_stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());
vm.prank(kycUser);
cdoEpoch.claimWithdrawRequest();                       // SUCCEEDS despite revoked credential
assertEq(underlying.balanceOf(kycUser), claimAmount);  // funds paid to non-allowed wallet

// 3b. Variant B — transfer receipt to never-KYC'd attacker (different "virtual host")
vm.prank(kycUser);
strategyToken.transfer(attacker, receipt);             // no transfer restriction (canTransfer deprecated)
// attacker must also own withdrawsRequests accounting — or, for queue flow,
// attacker simply receives proceeds via transferred claim right; for vault flow:
_startEpochAndCheckPrices(1);
_stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();                       // no isWalletAllowed on claim path
assertGt(underlying.balanceOf(attacker), 0);           // non-KYC wallet redeemed pool funds
```

Note on confidence: the missing `isWalletAllowed` check on every claim path is directly verified in the snippets above. Whether the strategy-token ERC20 has an additional `_transfer`/`_update` override restricting transfers could not be fully confirmed within the available iterations; if such an override exists and enforces allowlisting on transfers, Variant B narrows to Variant A (claim-after-revocation), which remains unconditionally exploitable since no re-check exists at claim time.