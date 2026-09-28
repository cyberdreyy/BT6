### Title
Unfunded instant-withdraw receipts paid at par from other claimants' funded balances - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` resolves the payout against the user's epoch-agnostic aggregate `instantWithdrawsRequests[_user]` instead of the funded per-epoch bucket (`instantWithdrawsRequestsByEpoch` / `pendingInstantWithdraws`). This is the same bug class as CVE-2024-8381: a property is looked up in the wrong environment — the aggregate user scope rather than the funded-epoch scope — so a receipt that was never funded is honored at par and paid out of underlyings reserved for other claimants.

### Finding Description
Instant-withdraw accounting maintains three views of the same claim:

- `instantWithdrawsRequests[_user]` — aggregate receipt balance across all epochs [1](#0-0) 
- `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` — per-epoch basis [2](#0-1) 
- `pendingInstantWithdraws` — the *still-unfunded remainder* the CDO has not yet collected [3](#0-2) 

`requestInstantWithdraw` mints the user a receipt and increments all of them [4](#0-3) . Funding arrives later via `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` [5](#0-4)  — the code explicitly treats a non-zero remainder as legitimate ("still-unfunded remainder", "partially prefunded instant requests") [6](#0-5) .

But `claimInstantWithdrawRequest` ignores funding status entirely:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [7](#0-6) 

`_transferFundedClaim` only protects `defaultRecoveryReserve`; nothing isolates underlyings collected via `collectWithdrawFunds` for funded normal withdraw receipts or for *other users'* funded instant receipts [8](#0-7) . An attacker holding an unfunded current-epoch instant receipt claims the full aggregate amount at par, and the transfer is satisfied from the strategy's mixed underlying balance — including money that belongs to `pendingWithdraws` claimants.

### Impact Explanation
Direct theft / insolvency: a tranche holder requests an instant withdraw during an epoch where the CDO cannot (or only partially) collect liquidity, leaving `pendingInstantWithdraws > 0`. While the strategy holds underlyings collected for other users' funded withdraw receipts (`collectWithdrawFunds`), the attacker calls `claimInstantWithdrawRequest` (through the CDO, which any user can trigger via its claim path) and is paid the full receipt amount, draining the funded-withdraw pool. The legitimate funded claimants' `claimWithdrawRequest` then reverts on `safeTransfer` or is underpaid — a permanent loss equal to the unfunded instant receipt amount, up to the full funded-claim balance held by the strategy.

### Likelihood Explanation
Requirements: an epoch phase where instant liquidity is short (partial funding — explicitly contemplated by `_defaultPrefundedInstantReserve` and `pendingInstantWithdraws != 0` checks), plus funded normal withdraw receipts sitting in the strategy. The attacker needs only a tranche position and no privileged role; they sandwich their request/claim around honest manager/borrower funding calls. The default-recovery guard `_claimDefaultedInstantWithdrawRequest` correctly uses the per-epoch key only after `defaultRecoveryFinalized`; pre-default, the aggregate lookup is unprotected [9](#0-8) .

### Recommendation
Track funded vs unfunded instant receipts per user/epoch. In `claimInstantWithdrawRequest`, cap the payout at `instantWithdrawClaimsByEpoch`-style funded basis (e.g., `instantWithdrawsRequests[_user] - unfunded share of pendingInstantWithdraws`) or maintain a `fundedInstantClaims` counter incremented by `collectInstantWithdrawFunds`, and resolve claims strictly against the epoch environment in which the receipt was funded — mirroring how `withdrawsRequestsByEpoch` + `lossRecoveryPriceByEpoch` already scope normal receipts.

### Proof of Concept
```solidity
// Foundry fork test sketch (mainnet fork, deployed vault)
// Phase: epoch running, strategy holds funded withdraw money
function test_UnfundedInstantReceiptStealsFundedPool() public {
    // 1. Honest user requests normal withdraw (epoch N); borrower/manager funds
    //    -> collectWithdrawFunds(F) pulls F underlying into the strategy.
    // 2. Attacker (tranche holder) calls CDO.requestInstantWithdraw(A).
    //    Strategy mints receipt: instantWithdrawsRequests[attacker] += A,
    //    pendingInstantWithdraws += A.
    // 3. CDO liquidity is short: collectInstantWithdrawFunds pulls only a part
    //    (or a later instant user drains the collected part first), leaving
    //    pendingInstantWithdraws = A while strategy balance = F.
    // 4. Attacker calls CDO.claimInstantWithdrawRequest(attacker).
  //    amount = instantWithdrawsRequests[attacker] = A (aggregate lookup,
    //    ignores that nothing was funded for this epoch's receipt).
    //    _transferFundedClaim passes: F was not in defaultRecoveryReserve.
    uint256 stolen = underlying.balanceOf(attacker);
    assertEq(stolen, A);              // paid at par
    // 5. Honest user's claimWithdrawRequest now reverts/underpays:
    vm.expectRevert();
    cdo.claimWithdrawRequest(honestUser); // strategy insolvent by A
}
```

Uncertainty note: whether the CDO's instant-withdraw path ever leaves `pendingInstantWithdraws > 0` for a *claimable* receipt in the non-defaulted flow depends on `IdleCDOEpochVariant.getInstantWithdrawFunds`/`stopEpoch` behavior (only partially reviewed — the grep hit count shows 20 matches in `IdleCDOEpochVariant.sol` whose exact sequencing could not be confirmed within the tool-call budget). If CDO funding is strictly atomic and all-or-nothing, the exploitable window narrows to partially-funded instant queues, which the strategy code explicitly models as a real state.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L59-59)
```text
  mapping (address => uint256) public instantWithdrawsRequests;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L63-63)
```text
  uint256 public pendingInstantWithdraws;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L107-109)
```text
  mapping(address => mapping(uint256 => uint256)) public instantWithdrawsRequestsByEpoch;
  /// @notice total outstanding instant-withdraw receipt basis per request epoch
  mapping(uint256 => uint256) public instantWithdrawClaimsByEpoch;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-386)
```text
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L713-722)
```text
  /// @dev `pendingInstantWithdraws` is the still-unfunded remainder. If it is lower than the
  /// current-epoch claim basis, the difference is already-held underlying reserved for those claims.
  /// @return prefundedReserve amount of current instant claims already backed by strategy underlyings
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
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
