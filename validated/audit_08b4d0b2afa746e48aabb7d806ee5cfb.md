### Title
Instant-withdraw receipt paid out before epoch funding, double-counting vault liquidity - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns a user's instant-withdraw receipt and transfers `instantWithdrawsRequests[_user]` underlying immediately, with no check that the request was actually funded via `collectInstantWithdrawFunds` and no epoch gating. Because `pendingInstantWithdraws` is only decremented by `collectInstantWithdrawFunds` (or by the default-recovery claim path), a receipt paid early is *still* counted as unfunded, so the borrower/CDO will fund it again at `stopEpoch` — the vault pays the same obligation twice, and the early payment is sourced from liquidity belonging to other claimants.

### Finding Description
The analog to the external "transfer whole balance instead of `_amount`" bug is a payout path that hands out more than the funded entitlement:

- `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to the user, and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (lines 356–375).
- `collectInstantWithdrawFunds(_amount)` is the *only* non-default path that reduces `pendingInstantWithdraws`, and it pulls the funded cash from the CDO (lines 398–403).
- `claimInstantWithdrawRequest(_user)` burns the receipt and calls `_transferFundedClaim(_user, amount)` where `amount = instantWithdrawsRequests[_user]` (lines 380–393). It never checks that `collectInstantWithdrawFunds` ran, nor any epoch boundary (contrast `_claimFundedWithdrawRequest` which requires `epochNumber > lastWithdrawRequest` at line 326).

`_transferFundedClaim` only isolates `defaultRecoveryReserve`; it does not segregate cash collected for normal funded withdraw claims (`collectWithdrawFunds`) or other pending obligations. So whenever the vault holds any underlying (e.g., borrower repayments parked between epochs, or funds collected for other users' claims), an attacker who holds a receipt can drain it immediately and *still* have `pendingInstantWithdraws` unchanged, forcing a second funding of the same amount at the next `stopEpoch`.

Attack sequence (buffer phase, normal mode):
1. Attacker (KYC'd lender holding tranche tokens / strategy-token exposure via CDO) calls the CDO path that triggers `requestInstantWithdraw(X, attacker)`. Receipt minted, `pendingInstantWithdraws += X`.
2. Vault holds ≥ X underlying (e.g., `collectWithdrawFunds` already pulled W for other users' funded claims, or borrower cash sits idle).
3. CDO calls `claimInstantWithdrawRequest(attacker)` — attacker receives X from the vault.
4. `pendingInstantWithdraws` still includes X. At `stopEpoch`, the borrower/CDO funds it again via `collectInstantWithdrawFunds`, and the attacker's "paid" request is counted as still outstanding.

### Impact Explanation
Direct theft / insolvency: each claim of an unfunded instant receipt pays X out of cash earmarked for other users' funded withdraw claims, and the unfunded counter still demands a second X from borrower funding at epoch stop. Quantified loss = X per receipt, bounded only by vault liquidity. Broken invariant: solvency / one receipt one payout.

### Likelihood Explanation
Requires only an unprivileged lender who can request an instant withdraw while the vault holds underlying. No privileged misbehavior needed; epoch-phase requirement is minimal (buffer or running). The main uncertainty: if protocol operation guarantees vault underlying balance is zero until `collectInstantWithdrawFunds` runs, the attack degrades to a revert — but nothing in the contract enforces that; `collectWithdrawFunds` and `finalizeDefaultRecovery` both leave spendable balance on the contract, and `sendInterestAndDeposits`/`safeTransferFrom` flows show cash routinely sits on the strategy.

### Recommendation
Track funded vs. unfunded instant claims. Either gate `claimInstantWithdrawRequest` on the request having been collected (e.g., per-epoch funded amounts analogous to `lossRecoveryPriceByEpoch`/`withdrawsRequestsByEpoch`, or a `fundedInstantWithdraws` counter decreased on claim), or decrement `pendingInstantWithdraws` inside `claimInstantWithdrawRequest` when paying from on-hand liquidity, so an early-paid receipt cannot be funded twice.

### Proof of Concept
Foundry fork sketch (vault = `IdleCreditVault`, CDO = `IdleCDOEpochVariant`, token = USDC):

```solidity
// setup: epoch running, attacker deposited via depositAA and holds tranche tokens;
// victim has a funded normal withdraw claim (collectWithdrawFunds pulled W into vault)
uint256 X = 100_000e6;
// 1. attacker requests instant withdraw through the CDO
vm.prank(attacker);
cdo.requestInstantWithdraw(X); // routes to strategy.requestInstantWithdraw(X, attacker)
assertEq(vault.pendingInstantWithdraws(), X);

// 2. vault already holds >= X underlying (victim's funded claim cash)
assertGe(usdc.balanceOf(address(vault)), X);

// 3. attacker claims immediately — no funding/epoch check
vm.prank(address(cdo));
vault.claimInstantWithdrawRequest(attacker);
assertEq(usdc.balanceOf(attacker), X);          // paid out of victim's funded cash
assertEq(vault.pendingInstantWithdraws(), X);   // still counted as unfunded!

// 4. stopEpoch: borrower funds pendingInstantWithdraws again via collectInstantWithdrawFunds
//    => X paid twice; victim's claimWithdrawRequest later reverts on insufficient balance
```

Key line to reproduce: `claimInstantWithdrawRequest` at `contracts/strategies/idle/IdleCreditVault.sol:380-393` performs no equivalent of the `epochNumber <= lastWithdrawRequest` gate in `_claimFundedWithdrawRequest` (line 326) and never touches `pendingInstantWithdraws`, which is only decremented in `collectInstantWithdrawFunds` (line 401) and `_claimDefaultedInstantWithdrawRequest` (line 852).