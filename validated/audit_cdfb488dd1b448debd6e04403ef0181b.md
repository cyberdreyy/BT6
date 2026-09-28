### Title
`requestWithdraw` burns tranche tokens but mints no withdraw receipt when the requested value rounds to zero - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The same "burn tokens, receive nothing" class from H-8 exists in `IdleCDOEpochVariant.requestWithdraw`. The tranche-to-underlying conversion rounds down via integer division and can yield `0`; `IdleCreditVault.requestWithdraw` then early-returns without minting the user any strategy-token receipt, while `_withdrawOps` still burns the caller's tranche tokens and updates NAV. The user permanently loses the position and the value is implicitly donated to the remaining tranche holders (NAV drops by 0 while supply drops by `_amount`, pushing the tranche price up).

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw`:

```solidity
// contracts/IdleCDOEpochVariant.sol:753-756
if (_amount == 0) {
  _amount = _userTrancheBal(msg.sender, _tranche);
}
_underlyings = _trancheToUnderlyings(_amount, _tranche);
```

with `_trancheToUnderlyings` at line 815-817:

```solidity
return _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN;
```

For any `_amount < ONE_TRANCHE_TOKEN / _tranchePrice`, `_underlyings` rounds to `0`. Both downstream paths then burn the tranche tokens without giving anything back:

- Normal path: `creditVault.requestWithdraw(0, msg.sender, 0)` hits the early return `if (_amount == 0) return;` in `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:246`), so no receipt is minted and nothing is added to `withdrawsRequests`/`pendingWithdraws`. Execution then continues to `_withdrawOps(_amount, principal = 0, _tranche)` (line 790), which calls `IdleCDOTranche(_tranche).burn(msg.sender, _amount)` (`contracts/IdleCDO.sol:513-526`).
- Instant-withdraw path (lines 761-768): `creditVault.requestInstantWithdraw(0, msg.sender)` burns/mints 0 in the vault, then `_withdrawOps(_amount, 0, _tranche)` still burns the user's tranche tokens.

There is no `toRedeem > 0` / `_underlyings > 0` check anywhere in the flow — exactly the missing guard the H-8 recommendation (`require(bptClaim > 0)`) addresses. `_checkIs0` only exists in the non-epoch `IdleCDO._withdraw` and only covers a zero `_amount`, not a zero computed payout.

The burn is also economically meaningful rather than a no-op: `_withdrawOps` reduces `lastNAVAA`/`lastNAVBB` by `principal` (0) while reducing total supply, so the unpayable claim is redistributed to remaining holders as a price increase — a forced donation of the victim's position.

### Impact Explanation
Any lender (KYC-passing user) calling `requestWithdraw` with an amount whose underlying value rounds to zero — most realistically `requestWithdraw(0, tranche)` on a small leftover tranche balance, or a request on a heavily impaired tranche where `_tranchePrice` is low — permanently loses the entire requested position and receives no withdraw receipt and no underlying. The loss is bounded by the largest `_amount` satisfying `_amount * _tranchePrice < ONE_TRANCHE_TOKEN` (up to just under 1 wei of underlying per call at healthy prices, and proportionally larger as the tranche price is impaired), but it is a 100% loss of the burned position and is repeatable. It also creates a griefing surface: any incentive for users to request small withdrawals (e.g., partial-balance UX) triggers the donation.

### Likelihood Explanation
Medium-low. At a healthy tranche price the exploit only triggers on dust-size requests, and `requestWithdraw` requires the epoch-phase withdrawal-request flow to be enabled (`allowAAWithdrawRequest`/`allowBBWithdrawRequest`, wallet allowed). No privileged or malicious role is needed — the caller is the victim — and no existing guard (skim, `_updateAccounting`, epoch gating, `NotAllowed` reverts) intercepts the zero-payout case. Likelihood rises materially when a tranche price is impaired (post-loss `_tranchePrice < ONE_TRANCHE_TOKEN / balance`), which is precisely when users are most likely to withdraw small residual balances.

### Recommendation
Revert when the computed withdrawal value is zero, before burning tranche tokens — mirroring the H-8 fix:

```solidity
// contracts/IdleCDOEpochVariant.sol, in requestWithdraw after _trancheToUnderlyings
_underlyings = _trancheToUnderlyings(_amount, _tranche);
_checkNotAllowed(_underlyings == 0);
```

Optionally also guard the post-fee amount (`principal + interest - totalFees == 0`) before calling `creditVault.requestWithdraw`, since a fee that fully consumes the requested amount produces the same burn-without-receipt outcome. The same `> 0` check should be applied to the instant-withdraw branch.

### Proof of Concept
Foundry test against the existing `IdleCreditVault.t.sol` harness (epoch stopped, requests enabled):

```solidity
function testRequestWithdrawBurnsForZeroReceipt() external {
  uint256 amountWei = 10000 * ONE_SCALE;
  idleCDO.depositAA(amountWei);              // healthy AA price ~1e6-scale vs 1e18 tranche units
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

  IdleCreditVault vault = IdleCreditVault(address(strategy));
  uint256 price = cdoEpoch.tranchePrice(address(AAtranche));
  uint256 dustAmt = (ONE_TRANCHE / price);   // any _amount with _amount*price < ONE_TRANCHE
  if (dustAmt == 0) dustAmt = 1;

  uint256 tranchePre = IERC20(address(AAtranche)).balanceOf(address(this));
  uint256 receiptPre = IERC20Detailed(strategyToken).balanceOf(address(this));

  uint256 req = cdoEpoch.requestWithdraw(dustAmt, address(AAtranche));

  assertEq(req, 0, 'computed underlyings rounded to 0');
  assertEq(IERC20(address(AAtranche)).balanceOf(address(this)), tranchePre - dustAmt, 'tranche tokens were burned');
  assertEq(IERC20Detailed(strategyToken).balanceOf(address(this)), receiptPre, 'no receipt minted');
  assertEq(vault.withdrawsRequests(address(this)), 0, 'nothing claimable');
  assertEq(vault.pendingWithdraws(), 0, 'vault owes nothing');
}
```

Expected result: the call succeeds, `dustAmt` tranche tokens are burned, and the user holds zero strategy-token receipt and zero `withdrawsRequests` — identical semantics to the Sherlock H-8 `if (bptClaim == 0) return 0` bug.