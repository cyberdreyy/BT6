### Title
Zero-value withdraw requests burn tranche tokens without minting any receipt - (contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` converts the caller's tranche tokens to underlyings and unconditionally burns the tranche tokens via `_withdrawOps`, even when the converted underlying amount is `0`. On the strategy side, `IdleCreditVault.requestWithdraw` early-returns on `_amount == 0` (`IdleCreditVault.sol:246`), so no receipt token is minted, `pendingWithdraws` is not increased, and `lastWithdrawRequest` is not set. The result is a "position" with zero claimable balance: the user's tranche tokens are destroyed and nothing claimable is created — the direct analog of opening a 0-leverage position that burns attached collateral.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol:739-791`:

```solidity
if (_amount == 0) {
    _amount = _userTrancheBal(msg.sender, _tranche);
}
_underlyings = _trancheToUnderlyings(_amount, _tranche);
...
creditVault.requestWithdraw(_underlyings, msg.sender, principal);
_withdrawOps(_amount, principal, _tranche);
```

`_trancheToUnderlyings` (`IdleCDOEpochVariant.sol:815-817`) computes `_amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN`, which rounds to `0` whenever `_amount * price < 1e18` — i.e., any user holding fewer than ~`1e18 / price` tranche wei (a dust balance from rounding during deposits, partial withdrawals, or fee/split rounding). There is no `require(_underlyings > 0)` or `require(_amount > 0)` guard.

When `_underlyings == 0`:
- `IdleCreditVault.requestWithdraw` returns immediately at `IdleCreditVault.sol:246` before burning principal, minting a receipt, or recording `withdrawsRequests`/`lastWithdrawRequest`.
- Back in the CDO, `_withdrawOps(_amount, 0, _tranche)` still burns the user's `_amount` tranche tokens (the tests confirm `_withdrawOps` burns the full tranche-token balance passed in, independent of the underlying value).

The instant-withdraw branch is affected the same way: `requestInstantWithdraw(0, msg.sender)` (`IdleCreditVault.sol:356-375`) has no zero check either — it mints a `0`-value receipt while `_withdrawOps` still burns the tranche tokens.

The same pattern exists in `claimWithdrawRequest`: since no `lastWithdrawRequest`/`withdrawsRequests` entries were recorded, the user has nothing to claim — the burn is permanent and unrecoverable, matching the report's "position locked forever / collateral burned" consequences.

### Impact Explanation
A user who calls `requestWithdraw(0, tranche)` (max-withdraw shorthand) or `requestWithdraw(dust, tranche)` with a tranche balance whose underlying value rounds to zero permanently loses those tranche tokens: no strategy-token receipt is minted, no `pendingWithdraws` liability is registered for the borrower, and no `lastWithdrawRequest` marker exists. The burned tranche supply also permanently desyncs tranche `totalSupply` from strategy-token accounting (`_withdrawOps` reduces `lastNAV` by `0` while removing tokens). As in the original report, no fees are collected because fee calculation is proportional to the (zero) underlying amount moved. The loss per call is capped at dust (< 1 underlying wei of value), but it is unrecoverable user collateral burned with zero accounting footprint — a broken "burn only against a minted receipt" invariant.

### Likelihood Explanation
Low. It requires a user to hold a tranche-token balance smaller than `ONE_TRANCHE_TOKEN / virtualPrice` (a ~1-wei-of-underlying dust position) and request a withdrawal on it — typically the tail of a partial withdrawal or rounding residue. No attacker can force a victim into this state; it is self-inflicted via user error, mirroring the report's likelihood of 1/10.

### Recommendation
Add an explicit non-zero check in `IdleCDOEpochVariant.requestWithdraw` after resolving the amount and price, e.g. `require(_underlyings != 0, "0")` (or `_checkNotAllowed(_underlyings == 0)`), before calling the vault and `_withdrawOps`. Symmetrically, revert on `_amount == 0` in `IdleCreditVault.requestInstantWithdraw` and `requestWithdraw` instead of silently no-oping, so the CDO can never burn tranche tokens for a request the vault ignored.

### Proof of Concept
Foundry fork PoC (against the credit-vault test harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPocZeroValueRequestBurnsTrancheTokens() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // start + stop an epoch so the pool is in buffer phase
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // leave the user a dust balance whose underlying value rounds to 0:
    // dust < ONE_TRANCHE_TOKEN / virtualPrice(AA)
    uint256 dust = (ONE_TRANCHE_TOKEN / idleCDO.virtualPrice(address(AAtranche))) - 1;
    deal(address(AAtranche), address(this), dust);
    // alternatively burn down the real balance to dust first

    uint256 tranchePre = IERC20Detailed(address(AAtranche)).balanceOf(address(this));
    uint256 receiptPre = IERC20Detailed(strategyToken).balanceOf(address(this));

    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // tranche tokens burned ...
    assertEq(IERC20Detailed(address(AAtranche)).balanceOf(address(this)), 0);
    // ... but zero receipt minted, zero pending liability, zero claim marker
    assertEq(requested, 0);
    assertEq(IERC20Detailed(strategyToken).balanceOf(address(this)), receiptPre);
    assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), 0);
    assertEq(IdleCreditVault(address(strategy)).withdrawsRequests(address(this)), 0);
    assertEq(IdleCreditVault(address(strategy)).lastWithdrawRequest(address(this)), 0);
}
```

Note: the exact dust bound depends on the live `virtualPrice`, and I did not fully read `_withdrawOps` internals — the conclusion relies on the established behavior (asserted in existing tests such as `testRequestWithdrawNormalWithPrevRequests`) that `_withdrawOps` burns the full `_amount` of tranche tokens passed to it regardless of the underlying value parameter.