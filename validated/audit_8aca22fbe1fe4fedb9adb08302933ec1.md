### Title
Fee-on-transfer underlyings break deposit accounting between `IdleCDOCreditVault` and `IdleCreditVault`, minting unbacked strategy tokens and draining other depositors - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CDO correctly mints tranche shares using the balance delta of the inbound transfer, but then forwards the **nominal** `_amount` to `IdleCreditVault.deposit`, which pulls the full nominal amount from the CDO and mints nominal strategy tokens. With a fee-on-transfer token this creates both an immediate drain of other users' funds and a permanently unbacked strategy-token supply.

### Finding Description
`IdleCDOCreditVault._deposit` (contracts/IdleCDOCreditVault.sol:191-212) measures the actually received amount via `_contractTokenBalance(_token) - _preBal` and mints shares only for that delta — the mitigation suggested in the original report. However, the next call is:

```solidity
// contracts/IdleCDOCreditVault.sol:210-211
// direct deposit in the strategy
IIdleCDOStrategy(strategy).deposit(_amount);
```

It passes the **user-supplied** `_amount`, not the received delta. `IdleCreditVault.deposit` then does:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:602-604
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_mint(msg.sender, _amount);
```

Two accounting breaks result when `token` burns a fee (e.g. STA, PAXG):

1. The CDO only received `_amount * (1 - f)` but the strategy pulls the full `_amount`, so the shortfall is taken from underlyings already held by the CDO that belong to other depositors (or the transfer reverts if the CDO balance is insufficient, DoS-ing all deposits).
2. The transfer into the strategy burns the fee *again*, so the strategy holds roughly `_amount * (1 - f)^2` while minting `_amount` strategy tokens to the CDO. Those strategy tokens back tranche NAV via `getContractValue()`, but the strategy's token balance can never cover them.

The same pattern exists in `IdleCDO._deposit` (contracts/IdleCDO.sol:251-258, behind the `directDeposit` flag).

### Impact Explanation
Insolvency of the credit vault. Strategy tokens are minted 1:1 against nominal deposits, but the strategy holds less underlying than its supply. Withdrawals (`collectWithdrawFunds`, `_transferFundedClaim`, `_transferDefaultRecovery`) pay receipts 1:1 from the strategy's token balance, so early claimants are paid in full while the last withdrawers — or default-recovery claimants via `defaultRecoveryReserve`/`defaultRecoveryPrice` — receive nothing. Loss scales with the cumulative fee burned across all deposits (e.g. ~2% per deposit at a 1% fee). No existing guard stops this: `_skimDonatedAssets` only handles unsolicited transfers, and the solvency invariant (strategy tokens ≤ strategy underlying balance) is never checked.

### Likelihood Explanation
Requires the vault's `token` to be fee-on-transfer. Deployed vaults use stablecoins, so this is conditional on pool-currency choice, but the code makes no such assumption and the failure is deterministic whenever it occurs — the very first deposit of sufficient size either drains prior depositors' funds or bricks the deposit path.

### Recommendation
Pass the actually received amount to the strategy and mint strategy tokens on the delta:

```solidity
uint256 received = _contractTokenBalance(_token) - _preBal;
_minted = _mintSharesAtCurrPrice(received, msg.sender, _tranche);
...
IIdleCDOStrategy(strategy).deposit(received);
```

and in `IdleCreditVault.deposit`:

```solidity
uint256 pre = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_mint(msg.sender, underlyingToken.balanceOf(address(this)) - pre);
```

Apply the same delta-based pattern to `collectWithdrawFunds`, `collectInstantWithdrawFunds`, `finalizeDefaultRecovery`'s pull, and `IdleCDO._deposit`'s `directDeposit` path, or explicitly document/enforce that fee-on-transfer tokens are unsupported.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
function testFeeOnTransferDeposit() public {
    // token: ERC20 mock burning 1% on every transferFrom/transfer
    vm.startPrank(alice);
    fot.approve(address(cdo), 100e18);
    cdo.depositAA(100e18);   // CDO receives 99e18
    // _deposit pulls: strategy.deposit(100e18)
    // strategy pulls 100e18 from CDO -> receives 99e18, mints 100e18 strategyTokens
    vm.stopPrank();

    // Invariant broken:
    uint256 stratTokens = vault.balanceOf(address(cdo)); // 100e18
    uint256 stratBal    = fot.balanceOf(address(vault)); // ~99e18 (fee burned twice)
    assertLt(stratBal, stratTokens);                     // unbacked supply
    // If alice was first depositor and CDO had no prior balance,
    // depositAA reverts instead (insufficient balance) -> deposit DoS.
}
```

Uncertainty: deployed `IdleCreditVault` instances target stablecoin pool currencies, so exploitability depends on vault configuration; also `depositDuringEpoch`/prefunded paths were not exhaustively traced but share the same nominal-amount forwarding pattern.