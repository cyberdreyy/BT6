### Title
Unfunded instant-withdraw receipts are paid from the vault's own underlying balance before funding is verified - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns the user's instant-withdraw receipt and pays out `instantWithdrawsRequests[_user]` from the strategy's underlying balance without first checking that those requests were actually funded. Funding happens separately through `collectInstantWithdrawFunds` (which decrements `pendingInstantWithdraws` and pulls tokens from the CDO at epoch start). This is the direct analog of CVE-2026-31631's "check the buffer size before checking the nonce": the claim consumes and pays the receipt (the "nonce") before validating that the funded buffer (the vault's collected instant-withdraw reserve) covers it.

### Finding Description
The relevant code:

- `requestInstantWithdraw` (IdleCreditVault.sol:356-375) burns CDO strategy tokens, mints a receipt to the user, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch` and `pendingInstantWithdraws`. No underlying moves at request time.
- `collectInstantWithdrawFunds` (IdleCreditVault.sol:398-403) is the only path that decrements `pendingInstantWithdraws` and transfers underlying into the vault (from the IdleCDO, which is expected to hold the prefunded liquidity).
- `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393) does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check that `amount` is covered by already-collected funds, no check against `pendingInstantWithdraws`, and no epoch/maturity check comparable to the `epochNumber <= lastWithdrawRequest[_user]` gate in `_claimFundedWithdrawRequest` (IdleCreditVault.sol:326-328). The only caller-side guard in `IdleCDOEpochVariant.claimInstantWithdrawRequest` (IdleCDOEpochVariant.sol:975-979) is `allowInstantWithdraw`.

The vault's underlying balance is not exclusively funded claim money: `deposit` (IdleCreditVault.sol:596-617) pulls fresh deposits into the strategy during the buffer period before they are forwarded to the borrower via `sendInterestAndDeposits`, and the balance also holds underlying collected for *other* users' funded claims. An unfunded instant receipt therefore pays out of deposits-in-transit or other users' reserves. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
Direct theft / insolvency. An unprivileged KYC-passed lender (or any wallet allowed by `isWalletAllowed`) can:

1. During the buffer period, deposit (or use existing tranche tokens) and call `requestInstantWithdraw` via the CDO for `_amount` up to the instant-withdraw cap.
2. Immediately call `claimInstantWithdrawRequest` on the CDO before the next `startEpoch` collects funds. The vault burns the receipt and transfers `_amount` of underlying taken from other users' deposits sitting in the strategy (or from reserve earmarked for other pending claims), even though `pendingInstantWithdraws` still records the request as unfunded.

Equivalently, when `pendingInstantWithdraws > 0` after a partially-funded epoch start, the unfunded portion of any receipt can still be claimed in full, draining the funded claims of honest users. The invariant broken is "one receipt one funded payout" — receipt redemption precedes the funding check.

Quantified loss: up to `min(instantWithdrawsRequests[attacker], vault underlying balance)` per request, repeatable across epochs.

### Likelihood Explanation
- Reachability: `requestInstantWithdraw` and `claimInstantWithdrawRequest` are callable by any allowed wallet through `IdleCDOEpochVariant` (`allowInstantWithdraw` only needs to be enabled by the honest manager, which is a supported mode per the prompt).
- The window is large: the entire buffer period plus any period where `pendingInstantWithdraws > 0` (partial prefunding, delayed `startEpoch`, borrower late repayment).
- No existing guard stops it: `_transferFundedClaim` is a plain transfer; the default-recovery branch only runs when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`; the epoch-wait check exists only in the normal (non-instant) claim path.

One caveat: I could not fully verify whether an off-path instant-delay timestamp (e.g., from `setInstantWithdrawParams`) is enforced inside `claimInstantWithdrawRequest` elsewhere in `IdleCDOEpochVariant`. From the function body at lines 975-979 no such check is present, but a delay enforced at request time would only narrow — not close — the unfunded-claim window.

### Recommendation
Track funded vs. unfunded instant receipts explicitly and validate before paying. In `claimInstantWithdrawRequest`, compute the funded portion (e.g., `claimable = min(instantWithdrawsRequests[_user], instantWithdrawClaimsByEpoch[epoch] - pendingInstantWithdraws)` or an explicit funded-per-epoch mapping populated by `collectInstantWithdrawFunds`), and only burn/pay that portion. Alternatively, gate the claim so it reverts while `pendingInstantWithdraws` covers the user's receipt, mirroring the epoch-maturity check used for normal withdraws.

### Proof of Concept
Foundry fork PoC sketch (structure follows `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_stopCurrentEpoch`, `_startEpochAndCheckPrices`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

function testUnfundedInstantClaimDrainsBufferDeposits() external {
    // Setup: AA/BB deposits exist, manager enabled instant withdraws
    // vm.prank(manager); cdoEpoch.setInstantWithdrawParams(0, type(uint256).max, true);

    // Phase: buffer period (epochEndDate != 0, epoch not running).
    // Honest deposits sit in the strategy pending forwarding at startEpoch.
    address honest = makeAddr('honest');
    _depositWithUser(honest, 100_000 * ONE_SCALE); // underlying now in IdleCreditVault

    address attacker = makeAddr('attacker');
    uint256 tranches = _depositWithUser(attacker, 50_000 * ONE_SCALE);

    // Attacker requests instant withdraw of the full position.
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(tranches, address(AAtranche));

    // No startEpoch / collectInstantWithdrawFunds happened:
    // pendingInstantWithdraws > 0, strategy received no funding.
    assertGt(strategy.pendingInstantWithdraws(), 0);

    // Bug: claim succeeds anyway, paying from the strategy's own balance
    // (i.e., 'honest' user's not-yet-forwarded deposit).
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - balPre, 0);

    // Invariant broken: underlying paid out exceeds funded reserves;
    // honest user's deposit is now short when forwarded to the borrower.
}
```

Expected on the vulnerable code: the claim transfers underlying while `pendingInstantWithdraws` remains positive and no `collectInstantWithdrawFunds` call occurred. After the fix, the call must revert or pay zero until funding is collected.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L975-979)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```
