### Title
Unfunded instant-withdraw receipts can drain underlying reserved for funded withdraw claims — `claimInstantWithdrawRequest` pays without verifying the request was actually funded — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

The kernel bug is a type/state confusion: an index is validated, but the slot it points to (`bearer_list[bid]`) is dereferenced without re-checking that it still holds an object of the expected type (a UDP bearer vs. a raw `dev`). The vault analog is `claimInstantWithdrawRequest` in `IdleCreditVault`: `instantWithdrawsRequests[_user]` acts as the indexed slot, but the claim path never verifies that the receipt is in the *funded* state before paying out underlying. Funding state is tracked only in the aggregate `pendingInstantWithdraws`, which `claimInstantWithdrawRequest` neither reads nor decrements — only `collectInstantWithdrawFunds` does.

### Finding Description

`requestInstantWithdraw` burns CDO strategy tokens, mints a 1:1 receipt to the user, and increments both `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (lines 356–374). Receipts become payable only after `startEpoch`, when the IdleCDO calls `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and pulls the underlying into the strategy (lines 398–403).

`claimInstantWithdrawRequest` (lines 380–392) then:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check that this user's receipt was funded — no read of `pendingInstantWithdraws`, no epoch-maturity gate, no per-epoch funded flag. `_transferFundedClaim` (lines 897–907) only protects `defaultRecoveryReserve`; any other underlying balance in the strategy is fair game. The strategy can legitimately hold underlying that does not belong to instant claimants: borrower-funded normal-withdraw claims awaiting `claimWithdrawRequest`, underlying deposited between epochs not yet forwarded to the borrower, and partially collected instant funds belonging to other users.

This mirrors the CVE directly: the epoch/index exists and the receipt exists, but the object behind the slot is in the wrong state ("unfunded" vs "funded"), and the code lacks the equivalent of the `media_id` check.

### Impact Explanation

An attacker (any KYC-passing lender) requests an instant withdraw during the buffer, then calls `claimInstantWithdrawRequest` before `collectInstantWithdrawFunds` has funded their receipt — e.g., while the epoch is still running, or after a startEpoch that only partially funded the instant queue. `_transferFundedClaim` pays them from underlying reserved for other users' already-funded normal withdraw claims. Those users' subsequent `claimWithdrawRequest` calls then revert on insufficient balance — direct theft of funded claim proceeds, quantified as `min(instantRequestAmount, strategyBalance - defaultRecoveryReserve)`. Additionally, `pendingInstantWithdraws` is never decremented on this path, so the stolen amount remains counted as an unfunded instant claim, corrupting `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` if a default is later finalized — inflating the recovery basis and diluting honest recovery claimants.

### Likelihood Explanation

The attack requires only an unprivileged KYC'd user and a window where the strategy holds underlying while an instant request is unfunded. That window exists every buffer period for any user holding tranche tokens when `allowInstantWithdraw` is enabled, and persists whenever borrower funding covers only part of the instant queue. Honest privileged roles (manager startEpoch/stopEpoch sequencing, borrower repayment) are sequenced around, not impersonated. No existing guard blocks it: `_transferFundedClaim` explicitly carves out only `defaultRecoveryReserve`, and the epoch-gating on `claimWithdrawRequest` (`epochNumber <= lastWithdrawRequest`) has no counterpart on the instant path.

### Recommendation

Track funded vs. unfunded instant receipts explicitly — e.g., decrement `pendingInstantWithdraws` inside `claimInstantWithdrawRequest` (reverting if it would underflow) or gate claims behind a per-epoch funded marker set by `collectInstantWithdrawFunds`. At minimum, `_transferFundedClaim` on the instant path should exclude both `defaultRecoveryReserve` and the still-unfunded portion of `pendingInstantWithdraws`.

### Proof of Concept

A Foundry fork PoC would: (1) have victim deposit AA and request a normal withdraw; (2) run start/stopEpoch so the borrower funds `pendingWithdraws` into the strategy; (3) during the next buffer, attacker deposits AA, calls `requestInstantWithdraw`, then calls `claimInstantWithdrawRequest` before the epoch starts / before `collectInstantWithdrawFunds` executes; (4) assert the attacker received underlying equal to their receipt while `pendingInstantWithdraws` still counts it, and the victim's later `claimWithdrawRequest` reverts or pays short. Note: I was unable to confirm in the available iterations whether `IdleCDOEpochVariant` adds an epoch-state gate around `claimInstantWithdrawRequest` beyond the `allowInstantWithdraw` flag at line 977 — if the CDO blocks the call while an epoch is running, the same theft still applies in the partially-funded-instant-queue variant (where `pendingInstantWithdraws != 0` after funding). If a CDO-side check exists that strictly orders funding before any claim, this finding would weaken to the double-counting/`defaultPendingClaimBasis` corruption only.