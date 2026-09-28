### Title
Instant-withdraw receipts pay out at par without checking that their epoch was funded, letting an unfunded claim drain underlyings collected for other claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays the full aggregate `instantWithdrawsRequests[_user]` at par from the strategy's underlying balance, even though only `collectInstantWithdrawFunds` (called by the CDO during claim processing) actually moves funded underlyings into the vault and decrements `pendingInstantWithdraws`. A user who holds an instant-withdraw receipt whose share was never collected can still claim it immediately, spending funds that were collected for other users' receipts — the storage equivalent of a use-after-free: the receipt is dereferenced as if its backing allocation still exists after the funding bucket was consumed.

### Finding Description
`requestInstantWithdraw` burns the CDO's strategy tokens, mints a 1:1 receipt to the user, and records the request in `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` [1](#0-0) . Funding arrives separately: `collectInstantWithdrawFunds` pulls underlyings from the CDO and reduces only `pendingInstantWithdraws` — it does not mark which receipts are funded [2](#0-1) .

`claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` in full and sends that amount via `_transferFundedClaim`, whose only guard is that the transfer does not dip into `defaultRecoveryReserve` [3](#0-2) . There is no check that the caller's receipts were actually covered by collected funds, no per-epoch funding gate (unlike normal requests, which require `epochNumber > lastWithdrawRequest`), and no decrement of `pendingInstantWithdraws` on claim [4](#0-3) . The invariant "one receipt, one collected payout" is broken whenever the CDO collects less than the aggregate instant-request basis in an epoch — e.g., partial borrower liquidity at `stopEpoch`, where `getInstantWithdrawFunds` collects only what is available.

### Impact Explanation
Direct theft with quantified loss. If two users request instant withdrawals of `A` and `B` in an epoch and the CDO collects only `A` (partial funding), user B — or an attacker who deliberately requested a large instant withdrawal knowing funding would be partial — calls `claimInstantWithdrawRequest` first and receives `B` underlyings from the vault, which physically holds only `A`. User A's funded receipt then permanently reverts (insufficient balance), so A loses `A` in full. The loss equals the unfunded receipt amount claimed; the attacker only needs to be a KYC-passing lender holding tranche tokens, an unprivileged actor under the stated model. This is theft of other claimants' funded withdrawals plus permanent freezing of the victim's receipt.

### Likelihood Explanation
The attack requires an epoch where instant-withdraw requests are processed but the collected amount is smaller than the aggregate request basis — i.e., the borrower/CDO returns less liquidity than requested, a routine stressed-market scenario that the prefunded-partial accounting (`_defaultPrefundedInstantReserve`, `pendingInstantWithdraws` remainder) explicitly anticipates [5](#0-4) . The attacker controls the timing (claim before the funded victim) and the amount (size of their own request). No privileged cooperation is needed; the honest manager merely processes requests and collects whatever is available. Races for the same pool among multiple unfunded claimants go to the first claim transaction.

### Recommendation
Track funded instant-withdraw amounts per epoch (e.g., `instantWithdrawFundedByEpoch`) or per user, and gate `claimInstantWithdrawRequest` so a receipt is payable only up to the amount actually collected for its epoch — mirroring the `epochNumber > lastWithdrawRequest` maturity gate used for normal requests. Alternatively, decrement `instantWithdrawsRequests`/`instantWithdrawClaimsByEpoch` at funding time for the unfunded remainder (converting it to a loss-adjusted or defaulted receipt), so unfunded claims can never draw down the collected pool at par.

### Proof of Concept
Foundry fork PoC sketch (unverified end-to-end — CDO-side entry points and partial-funding path were not fully traced within available iterations):

```solidity
// Assume epoch running, instant withdrawals enabled via setInstantWithdrawParams.
// Users A (victim) and B (attacker) each deposit and hold tranches.

// 1. Epoch N running: A and B call requestInstantWithdraw via the CDO.
cdoEpoch.requestInstantWithdraw(amountA, AAtranche); // as A
cdoEpoch.requestInstantWithdraw(amountB, AAtranche); // as B
// instantWithdrawsRequests[A] == amountA, [B] == amountB

// 2. stopEpoch with reduced borrower repayment; CDO collects only amountA.
//    getInstantWithdrawFunds/collectInstantWithdrawFunds pull amountA into the vault;
//    pendingInstantWithdraws still carries the unfunded amountB remainder.

// 3. Attacker claims first — no funding check on the receipt.
cdoEpoch.claimInstantWithdrawRequest(); // as B
// B receives amountB underlyings although only amountA was collected:
// vault balance is now ~0 (or reserve-only), pendingInstantWithdraws unchanged.

// 4. Victim claims — reverts or receives 0 despite a fully funded receipt.
vm.expectRevert();
cdoEpoch.claimInstantWithdrawRequest(); // as A: _transferFundedClaim underflows balance
```

Uncertainty I could not fully resolve with remaining iterations: (a) whether `IdleCDOEpochVariant`'s instant-withdraw processing can in practice collect less than the aggregate `instantWithdrawClaimsByEpoch` for an epoch (the code's own `pendingInstantWithdraws`-remainder and prefunded-reserve logic strongly suggests partial collection is a designed-for state); (b) whether `requestInstantWithdraw`/claim remains reachable post-`defaultRecoveryFinalized`, which would additionally let new receipts written into `epochNumber == defaultRecoveryEpoch` be paid from `defaultRecoveryReserve` at `defaultRecoveryPrice` via `_claimDefaultedInstantWithdrawRequest` — a second, related stale-epoch drain worth auditing.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```
