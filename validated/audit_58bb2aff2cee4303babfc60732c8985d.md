### Title
`claimInstantWithdrawRequest` pays receipts without epoch/funding gating and never clears `pendingInstantWithdraws` — double funding of instant-withdraw receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns the user's entire `instantWithdrawsRequests[_user]` receipt balance and pays it 1:1 via `_transferFundedClaim`, with no check that the request's epoch has been funded and without decrementing `pendingInstantWithdraws`. A later `collectInstantWithdrawFunds` still pulls the full pending amount from the CDO/borrower, so the same receipt is effectively paid twice.

### Finding Description
The CVE-2020-6428 bug class is a use-after-free: state consumed after it was logically freed. The vault analog is a withdrawal receipt consumed before its backing state is retired.

Normal withdraws are correctly gated: `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` (contracts/strategies/idle/IdleCreditVault.sol:326) and `_settleApr0`/`withdrawsRequests` are cleared on claim.

Instant withdraws have no equivalent guard. `requestInstantWithdraw` mints the receipt and increments both `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:356-375). The borrower only funds `pendingInstantWithdraws` when `collectInstantWithdrawFunds` is called by the CDO at the next `startEpoch` (contracts/strategies/idle/IdleCreditVault.sol:398-403). But `claimInstantWithdrawRequest` (contracts/strategies/idle/IdleCreditVault.sol:380-393):

1. reads `amount = instantWithdrawsRequests[_user]`,
2. burns it and zeroes the per-user mapping,
3. calls `_transferFundedClaim(_user, amount)` immediately,
4. never touches `pendingInstantWithdraws`.

There is no `epochNumber`-vs-request-epoch check like the normal path, and no per-request funding marker. An attacker (any KYC-passing tranche holder) can:

- Epoch running: deposit, receive tranche tokens.
- Call `requestInstantWithdraw` via `IdleCDOEpochVariant.requestInstantWithdraw` — receipt minted, `pendingInstantWithdraws += amount`.
- In the same block call `claimInstantWithdrawRequest` — paid `amount` at par from underlying already sitting in the vault (funded-but-unclaimed claims of other users, collected withdraw funds, etc.).
- At next `startEpoch`, the honest CDO calls `collectInstantWithdrawFunds(pendingInstantWithdraws)` which still includes the attacker's amount — the borrower transfers that underlying a second time.

The receipt is "freed" (burned) but its liability record `pendingInstantWithdraws` is still live — the UAF analog. Existing guards do not stop this: `_claimFundedWithdrawRequest`'s epoch gating does not apply to instant claims, and `allowInstantWithdraw` only gates the CDO entry point, not funding freshness.

### Impact Explanation
Direct theft/insolvency: the borrower funds each instant receipt twice (once drained early from the vault's funded-claim balance, once via `collectInstantWithdrawFunds`), or, if the vault lacks balance at claim time, earlier funded claimants' reserves are consumed and their legitimate claims become unpayable. Loss equals the attacker's instant-withdraw amount, bounded only by the vault's instant-withdraw cap (`setInstantWithdrawParams`) and tranche price.

### Likelihood Explanation
Requires `allowInstantWithdraw == true` and instant mode enabled — a supported configuration exercised in `testClaimInstantWithdrawRequest`. The attacker needs only tranche tokens obtained via a normal KYC'd deposit; both calls are unprivileged and can be atomically sequenced. Likelihood is gated by whether an instant-withdraw delay (`instantDelay`) is enforced inside the CDO for the non-queue path — I could not fully verify that check exists outside `IdleCDOEpochQueue.processWithdrawalClaims`; if the delay is only enforced by the queue, the direct `IdleCDOEpochVariant.claimInstantWithdrawRequest` path is exposed.

### Recommendation
Track the request epoch for instant withdrawals (e.g., `lastInstantWithdrawRequest[_user] = epochNumber` or reuse `instantWithdrawsRequestsByEpoch`) and revert claims until `epochNumber` has advanced past the funding epoch, mirroring `_claimFundedWithdrawRequest`. Additionally decrement `pendingInstantWithdraws` (and `instantWithdrawClaimsByEpoch`) inside `claimInstantWithdrawRequest` for any portion already funded, so a claim can never leave the borrower's obligation intact.

### Proof of Concept
Foundry fork test sketch (against `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// enable instant withdraws via manager, startEpoch
cdoEpoch.setInstantWithdrawParams(0, type(uint256).max, true); // delay 0
uint256 minted = idleCDO.depositAA(1000e18);
// attacker requests instant withdraw mid-epoch
cdoEpoch.requestInstantWithdraw(minted, address(AAtranche));
uint256 pendingBefore = strategy.pendingInstantWithdraws();
// claim immediately, before collectInstantWithdrawFunds runs
cdoEpoch.claimInstantWithdrawRequest();
assertEq(strategy.instantWithdrawsRequests(address(this)), 0);
// pendingInstantWithdraws is unchanged — borrower will fund it again at next startEpoch
assertEq(strategy.pendingInstantWithdraws(), pendingBefore);
```

Note: end-to-end verification requires confirming whether `instantDelay` is enforced outside the queue path; if it is enforced in `IdleCDOEpochVariant`, the exploit window shifts to "claim after funding" and the `pendingInstantWithdraws` non-decrement still leaves stale liability that inflates the next `collectInstantWithdrawFunds` pull.