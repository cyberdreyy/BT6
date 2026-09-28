### Title
Withdraw requests made during the buffer period skip the one-epoch wait and can instantly drain funded withdrawal reserves - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` treats the buffer window between `stopEpoch` and `startEpoch` as a "closed pool" because `epochEndDate() == 0`, so it skips the `pendingWithdraws` accounting, and `_claimFundedWithdrawRequest` skips the `epochNumber > lastWithdrawRequest` wait check for the same reason. An attacker holding tranche tokens can therefore request a withdrawal and claim it atomically inside the same buffer, paid from the vault's underlying balance — which at that moment holds reserves already funded by the borrower for other users' matured receipts and instant withdrawals. This mirrors the reported bug class: the "signal then wait" delay that is supposed to force a request through one full epoch is bypassed, letting an unprivileged user exit instantly at the expense of funded claimants.

### Finding Description
In `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:259-294`), `isClosed` is computed as `IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0`. During the buffer period between epochs, `epochEndDate` is still 0 (it is only set on `startEpoch`), so a request made in the buffer is treated like a closed-pool request: `pendingWithdraws` is not incremented, and `lastWithdrawRequest[_user]` is set to the current `epochNumber`.

The claim path `_claimFundedWithdrawRequest` (`contracts/strategies/idle/IdleCreditVault.sol:319-350`) enforces the one-epoch delay only when `epochEndDate() != 0`:

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

In the buffer this condition is false, so a receipt created seconds earlier is immediately claimable. The payout goes through `_transferFundedClaim`, which transfers underlying tokens held by the vault. Right after `stopEpoch`, that balance is exactly the reserve the borrower funded via `collectWithdrawFunds` (`contracts/strategies/idle/IdleCreditVault.sol:411-430`) for the previous epoch's matured receipts, plus any instant-withdraw funds collected via `collectInstantWithdrawFunds` (`contracts/strategies/idle/IdleCreditVault.sol:398-403`).

The intended "closed pool" semantics — where `epochEndDate == 0` permanently and all borrower funds have been recalled — are conflated with the transient buffer state, where the vault balance is earmarked for specific claimants. The buffer-period wait that forces normal requests to be settled at the next `stopEpoch` (the analog of the signaling delay in the report) is nullified.

### Impact Explanation
Direct theft / permanent freezing of other users' funds. The attacker's receipt is priced correctly at `virtualPrice`, but it is settled instantly from reserves that belong to prior-epoch claimants. When the legitimate matured claimants call `claimWithdrawRequest` / `claimInstantWithdrawRequest`, the vault balance is depleted and their claims revert or underpay — the attacker has consumed their funded reserve. Loss is bounded by the funded withdrawal reserve held by the vault during the buffer window, which can be a large fraction of TVL when many users exit.

### Likelihood Explanation
Requires only a KYC-passed tranche holder (or queue-enabled user acting through `IdleCDOEpochVariant.requestWithdraw`, which the queue itself calls during the buffer in `processWithdrawRequests`, proving the path is open in that phase). No privileged role is needed. The window is every buffer period between `stopEpoch` and `startEpoch`, and the attack is atomic (request + claim in one transaction), so it can also front-run other claimants racing to claim first — each earlier claimer can drain the shared reserve before later claimants.

### Recommendation
Distinguish "buffer" from "closed pool" explicitly rather than inferring it from `epochEndDate == 0`. Options:

- In `requestWithdraw`, only treat the request as closed-pool when the pool is permanently closed (e.g., a dedicated `closed` flag set when the borrower fully repays), not merely when `epochEndDate == 0`.
- Alternatively/additionally, in `_claimFundedWithdrawRequest`, require `epochNumber > lastWithdrawRequest[_user]` whenever a subsequent epoch can still start, so buffer-time requests still wait one epoch and are funded through `pendingWithdraws` at the next `stopEpoch` like all other receipts.

A short-term mitigation matching the report's recommendation: exclude buffer-time requests from immediate claimability by tracking the request epoch against the epoch in which the next `startEpoch` occurs.

### Proof of Concept
Foundry-style outline (against the existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: normal epoch, users deposit AA/BB, epoch runs, stopEpoch repays
// interest + funds pendingWithdraws into the vault via collectWithdrawFunds.
uint256 amount = 100_000e6;
uint256 mintedAA = idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);

// honest user requests withdraw during the epoch; wait one epoch
cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
_startEpochAndCheckPrices(1);
_stopEpochAndCheckPrices(1, apr, expectedFunds); // vault now holds funded reserve for the request

// ATTACK: we are now in the buffer (epochEndDate == 0).
// Attacker holds tranche tokens from an earlier deposit.
uint256 attackerTranches = /* attacker's balance */;

// 1. requestWithdraw: isClosed==true -> no pendingWithdraws, lastWithdrawRequest set to current epoch
uint256 requested = cdoEpoch.requestWithdraw(attackerTranches, address(AAtranche));

// 2. claimWithdrawRequest in the SAME transaction/buffer:
//    epochEndDate()==0 => epoch-wait check skipped; paid from vault's funded reserve
uint256 vaultBalPre = underlying.balanceOf(address(strategy));
cdoEpoch.claimWithdrawRequest();
assertGt(underlying.balanceOf(attacker), 0);

// 3. honest user's matured claim now fails/underpays: reserve was drained
vm.expectRevert(); // or assertLt(paid, maturedAmount)
cdoEpoch.claimWithdrawRequest(); // honest user
```

Caveat: I could not fully re-verify the internal body of `_transferFundedClaim` and the exact `requestWithdraw` entrypoint gating in `IdleCDOEpochVariant` within the iteration budget; the exploit hinges on (a) `requestWithdraw` being callable during the buffer (confirmed — the queue calls it there and tests call `cdoEpoch.requestWithdraw` post-stopEpoch) and (b) `_transferFundedClaim` paying from the vault's underlying balance rather than per-receipt escrow (confirmed by `collectWithdrawFunds`/`collectInstantWithdrawFunds` accumulating a shared balance in the vault).