### Title
Stale write-off quotes are fulfillable across epoch/loss boundaries, letting a fulfiller buy tranche tokens at an outdated price - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` is gated on `isEpochRunning()` (IdleCreditVaultWriteOffEscrow.sol:88), but `fullfillWriteOffRequest` performs no epoch-phase or freshness check at all (IdleCreditVaultWriteOffEscrow.sol:123-131). A lender's fixed-price offer (`tranches` → `underlyings`), signed during a running epoch, stays executable after the epoch stops, after a loss-adjusted `stopEpochWithDuration`, after a borrower default, or after an epoch where interest accrual raised the tranche price. The attacker (any EOA, e.g., a KYC'd lender acting as fulfiller) fills the stale quote when the market value of the escrowed tranche tokens exceeds the stale `underlyings` price, extracting the spread from the lender — the analog of PyWBEM validating a certificate without checking it against the *current* expected identity.

### Finding Description
The escrow stores a single per-user `WriteOffRequest { tranches, underlyings }` (lines 24-29). Creation is phase-gated:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol:86-90
function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
  if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
  if (amount == 0) revert NotAllowed();
```

Fulfillment validates only that the stored request matches the caller-supplied amounts and that the payment is not below the stored ask (line 129: `_underlyings < currentRequest.underlyings` → revert). There is:

- no `isEpochRunning` / epoch-number check at fulfillment time,
- no expiry or epoch-binding recorded in the request,
- no re-check of tranche `virtualPrice` against the agreed `underlyings` amount.

The lender's escape is `deleteWriteOffRequest` (line 105), which is also ungated by phase, so the design implicitly assumes lenders monitor and cancel. But cancellation is a separate transaction: between a loss/interest event becoming visible (e.g., `stopEpoch` transaction in the mempool, or simply the last block before `epochEndDate` where `createWriteOffRequest` still succeeds) and the lender's cancellation tx landing, any fulfiller can atomically fill the stale quote. The check (price agreed at request time) and the use (value transfer at fill time) occur in different epoch states — a textbook TOCTOU / stale-verification flaw.

Concretely: tranche price drifts upward during an epoch because `virtualPrice` accrues `expectedEpochInterest`. A lender who priced 10,000 tranche tokens at 10,000 USDC at epoch start is offering a discount equal to the accrued interest by late epoch. A fulfiller pays `10,000e6 - exitFee`, receives `10,000e18` tranche tokens now redeemable for ~`10,000e6 * (1 + apr * elapsed / YEAR)` via `claimWithdrawRequest`/`claimInstantWithdrawRequest` once the epoch cycles, pocketing the accrued-interest delta that belonged to the lender's principal pricing decision. The lender cannot reprice atomically; any re-price requires a second transaction that can be front-run by a fill.

### Impact Explanation
Direct theft of the spread between stale quote price and current tranche value, bounded by the lender's posted size and the drift in `virtualPrice` since the request was created. For a lender who requested a write-off early in a long epoch at par, the fulfiller captures nearly a full epoch of tranche interest (potentially several percent at the configured `lastApr`, up to `maxApr` of 20%). The lender receives `underlyings - exitFee` for tokens worth more, and because fulfillment clears the request and transfers tokens in one tx, there is no recovery path. Loss is quantifiable: `profit = trancheValueNow - staleUnderlyingsPaid`.

### Likelihood Explanation
Medium-low. It requires a lender to leave a request open while tranche value drifts (or to post it near epoch end), and a fulfiller willing to front-run fills — but no privileged cooperation is needed, the fulfiller needs no KYC (`fullfillWriteOffRequest` is callable by "any wallet" per line 122, confirmed by `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw`), and the capital outlay is limited to the stale ask price. The bug is deterministic once a stale request exists.

### Recommendation
Bind each request to the epoch in which it was created (store `epochNumber` or `epochEndDate` at creation; revert fulfillment if the epoch advanced or is no longer running), or require `isEpochRunning()` in `fullfillWriteOffRequest` matching the create-side gate. Alternatively, store a user-chosen expiry timestamp and reject fills after it.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
function testStaleQuoteFilledAfterInterestAccrual() external {
  uint256 reqTranches = 10_000e18;
  uint256 reqUnderlyings = 10_000e6;

  // Epoch running: LP posts a par-priced write-off request.
  vm.prank(LP);
  escrow.createWriteOffRequest(reqTranches, reqUnderlyings);

  // Time passes within the same epoch: virtualPrice accrues expectedEpochInterest.
  vm.warp(block.timestamp + epochDuration - 1 days);

  // Attacker (non-KYC fulfiller) fills the stale quote at par.
  address attacker = makeAddr("attacker");
  deal(address(underlying), attacker, reqUnderlyings);
  vm.startPrank(attacker);
  underlying.approve(address(escrow), reqUnderlyings);
  escrow.fullfillWriteOffRequest(LP, reqTranches, reqUnderlyings);
  vm.stopPrank();

  // Attacker's tranche tokens are now worth more than reqUnderlyings at current price.
  uint256 attackerTranches = tranche.balanceOf(attacker);
  uint256 valueNow = cdoEpoch.tranchePrice(address(tranche)) * attackerTranches / 1e18;
  assertGt(valueNow, reqUnderlyings, "attacker bought below current tranche value");
}
```

The same stale-quote window exists across `stopEpoch`/`stopEpochWithDuration` and post-default states since fulfillment never revalidates phase; the interest-accrual direction is the profit-positive one for the fulfiller.

Caveat: the protocol may treat open-ended OTC orders as intended and place cancellation duty on the lender; the reportable core is the asymmetric gating — creation requires a running epoch while fulfillment does not — which the codebase does not document or test as deliberate.