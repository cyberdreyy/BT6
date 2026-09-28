### Title
Division by zero in `_virtualPriceAux` when a tranche supply hits 0 with residual NAV permanently freezes the vault - ([File: contracts/IdleCDO.sol])

### Summary
`IdleCDO._virtualPriceAux` splits the tranche NAV per token by dividing by `trancheSupply` without checking that the *current* tranche's supply is non-zero. It only checks whether the *other* tranche's supply is zero (line 394) to decide gain attribution. If a tranche's `totalSupply` reaches 0 while its saved NAV (`lastNAVAA`/`lastNAVBB`) is still non-zero (rounding dust left by `_withdrawOps`), every subsequent call that triggers `_updateAccounting` reverts with a division by zero, permanently freezing all deposits, withdrawals and harvests — including the other tranche's funds. The credit-vault variant (`IdleCDOCreditVault.sol:296-297`) explicitly guards `trancheSupply == 0`, but the base `IdleCDO` does not.

### Finding Description
In `contracts/IdleCDO.sol:391-429`, `_virtualPriceAux` computes:

```solidity
if (_trancheSupply(_isAATranche ? _BBTranche : _AATranche) == 0) {
  _totalTrancheGain = totalGain;
} else {
  ...
}
_virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
```

`trancheSupply` is the supply of the tranche being priced. The zero-supply check on line 394 only covers the *counterpart* tranche. There is no early return equivalent to the one added in `IdleCDOCreditVault.sol`:

```solidity
uint256 trancheSupply = _trancheSupply(_tranche);
if (trancheSupply == 0) return (oneToken, 0);
```

Reachability of `trancheSupply == 0 && _lastTrancheNAV > 0`: `_withdraw` burns the user's full tranche balance and subtracts `toRedeem = _amount * _tranchePrice / ONE_TRANCHE_TOKEN` from the tranche's NAV in `_withdrawOps`. Because redemption rounds down, the last BB holder can burn 100% of BB supply while `lastNAVBB` remains a few wei. On the next `totalGain != 0` accounting update (any deposit, withdraw or harvest that moves NAV), `_virtualPriceAux(BB)` executes line 429 with `trancheSupply == 0` and reverts.

`_updateAccounting` is invoked inside `_deposit`, `_withdraw`/`_withdrawOps` and `harvest` paths, so once the dust-NAV/zero-supply state is reached, every state-changing entry point reverts permanently — including `depositAA`/`withdrawAA` for unrelated senior holders. The loss-socialization branch (`_juniorTVL` path) cannot rescue the state because the revert happens at the final price division regardless of gain sign. Skim/donation isolation, `nonReentrant`, and epoch gating do not prevent it.

### Impact Explanation
High: permanent freezing of all remaining funds in the CDO. After the dust state is created, no deposit, withdrawal, or harvest can execute; AA holders' principal and accrued yield are locked indefinitely (there is no admin function to resurrect a tranche's supply or overwrite `lastNAVBB`). Quantified loss = the entire residual TVL of the vault at the moment of freeze.

### Likelihood Explanation
Medium. An attacker only needs to be an ordinary tranche-token holder (unprivileged). They deposit BB (or buy BB tokens), wait until they are the sole BB holder (or coordinate so they hold the last units), then redeem their full balance. Integer division in `_tranchePrice`/redeem math makes leftover wei-level NAV the common case rather than an exception, so the zero-supply/nonzero-NAV state is reproducible without privileged roles, donations, or oracle manipulation. Note I could not fully confirm whether `harvest`/other entry points reach `_virtualPriceAux` for a zero-supply tranche before some earlier guard in all variants — the exact reachable subset should be confirmed in the PoC — but the deposit/withdraw accounting path clearly does.

### Recommendation
Add the same guard used in `IdleCDOCreditVault._virtualPriceAux` to the base `IdleCDO._virtualPriceAux`: return `(oneToken, 0)` (or the saved price) when `trancheSupply == 0`. Alternatively, snap the tranche NAV to 0 in `_withdrawOps` when the tranche's `totalSupply` becomes 0, so saved NAV can never outlive supply.

### Proof of Concept
Foundry fork PoC outline (fork mainnet at a block where a live `IdleCDO` instance exists, or use the repo's existing deployment fixtures under `test/foundry/`):

```solidity
// 1. Attacker deposits into BB tranche: cdo.depositBB(amount)
//    (KYC not required for base IdleCDO)
// 2. Ensure attacker is sole BB holder: have other holders withdrawBB,
//    or start from a fresh instance where attacker is the only depositor.
// 3. Attacker calls withdrawBB with their full balance:
//      uint256 bal = IdleCDOTranche(cdo.BBTranche()).balanceOf(attacker);
//      cdo.withdrawBB(bal);
//    -> supply(BB) == 0, but lastNAVBB retains dust (assert lastNAVBB > 0).
// 4. Any user (or honest keeper) calls depositAA / withdrawAA / harvest.
// 5. _updateAccounting -> _virtualPriceAux(BB) reaches line 429
//      ... / trancheSupply  with trancheSupply == 0
//    -> vm.expectRevert() on every subsequent deposit/withdraw/harvest.
// 6. Assert AA holder funds are unrecoverable: virtualPrice(AA) still shows
//    value, but withdrawAA always reverts.
```

The key assertions: after step 3, `IdleCDOTranche(BB).totalSupply() == 0 && cdo.lastNAVBB() > 0`; after step 4, `depositAA`, `withdrawAA` and `harvest` all revert with a divide-by-zero panic.