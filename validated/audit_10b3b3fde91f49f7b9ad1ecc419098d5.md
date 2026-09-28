### Title
`IdleCreditVault.deposit` mints strategy tokens 1:1 for the gross `_amount`, breaking strategy-token backing when `token` is a fee-on-transfer ERC20 — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The credit-vault strategy mints receipt/strategy tokens for the nominal `_amount` pulled from the IdleCDO, not for the amount actually received. With a fee-on-transfer `token`, every deposit inflates the strategy-token supply relative to real collateral, and the shortfall is silently absorbed by other LPs' funds held in the IdleCDO.

### Finding Description
`IdleCreditVault.deposit(_amount)` is the strategy-side hook called by `IdleCDOCreditVault._deposit` after the CDO pulls underlying from the user. It executes `underlyingToken.safeTransferFrom(msg.sender, address(this), _amount)` and then `_mint(msg.sender, _amount)` — minting exactly `_amount` strategy tokens regardless of what the transfer actually delivered.

In `IdleCDOCreditVault._deposit` (contracts/IdleCDOCreditVault.sol:203-211), the CDO correctly mints tranche shares from the *received* balance delta (`_contractTokenBalance(_token) - _preBal`), but then calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the gross amount. With a 1% fee-on-transfer token and `token` set to that asset:

1. User deposits 100 tokens; CDO receives 99 and mints ~99 worth of tranche shares to the user.
2. CDO calls `strategy.deposit(100)`; the strategy pulls 100 from the CDO but receives only 99, while minting 100 strategy tokens to the CDO.

Two correlated breaks result:
- The CDO paid out 100 underlying but was only refilled 99 by the depositor — a net drain of the fee amount sourced from pre-existing vault collateral each deposit.
- The strategy-token supply (the CDO's recorded claim on the credit vault / borrower) is inflated by the fee amount relative to underlying actually forwarded. That gap is socialized across all tranche holders at `stopEpoch`/default-finalization time, where claims are settled against real balances.

The same gross-amount assumption exists in `collectWithdrawFunds` (line 428) and `collectInstantWithdrawFunds` (line 402): both clear `pendingWithdraws`/`pendingInstantWithdraws` and pull `_amount` via `safeTransferFrom` without measuring received tokens, so a fee-on-transfer collateral would leave receipts underfunded while accounting treats them as fully funded.

### Impact Explanation
Each deposit destroys `fee% * _amount` of backing. The minted-but-unbacked strategy tokens dilute every other tranche holder and withdraw-request claimant: at epoch stop, `collectWithdrawFunds` / `collectInstantWithdrawFunds` / default recovery (`finalizeDefaultRecovery`, `defaultRecoveryPrice`) all settle pro-rata against a total basis that includes phantom tokens. Repeating deposit → `requestWithdraw` cycles drains the vault at 1:1 cost to the attacker — not directly profitable, but it breaks the core solvency invariant and lets a coordinated set of unprivileged KYC'd lenders drain the collateral of honest depositors, leaving last claimants permanently underpaid or frozen. This is the same "1:1 claim-to-collateral ratio broken" impact as the reference `Queue` report, mapped onto the credit-vault's 1:1 strategy-token-to-underlying invariant.

### Likelihood Explanation
Likelihood is conditional: it requires the vault `token` to be a fee-on-transfer asset. The reference report explicitly notes USDT-style tokens whose fee is currently zero but can be enabled later; nothing in `initialize` or strategy setup restricts `token` to feeless ERC20s. Once such a fee is nonzero, no privileged action is needed — any lender calling `depositAA`/`depositBB` triggers the inflation.

### Recommendation
In `IdleCreditVault.deposit`, measure the received delta instead of trusting `_amount`:

```solidity
uint256 _preBal = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
uint256 _received = underlyingToken.balanceOf(address(this)) - _preBal;
_mint(msg.sender, _received);
```

Apply the same balance-delta check in `collectWithdrawFunds` and `collectInstantWithdrawFunds` (or explicitly document that fee-on-transfer tokens are unsupported as vault currency). Alternatively, have the CDO pass the net received amount to `strategy.deposit` and mint only that.

### Proof of Concept
Foundry fork test sketch against a mock fee-on-transfer ERC20 as `token`:

```solidity
function testFoTDepositInflatesStrategyTokens() public {
    // FoTToken: 1% burn on every transfer
    FoTToken fot = new FoTToken();
    // deploy IdleCDOCreditVault + IdleCreditVault with token = fot,
    // seed vault with honest LP capital so CDO holds > deposit amount

    uint256 cdoBalBefore = fot.balanceOf(address(cdo));
    uint256 stratSupplyBefore = vault.totalSupply();

    fot.mint(attacker, 100e6);
    vm.startPrank(attacker);
    fot.approve(address(cdo), 100e6);
    cdo.depositAA(100e6); // attacker shares minted on net 99
    vm.stopPrank();

    // CDO paid 100, received 99 -> net -1 drained from prior collateral
    assertEq(fot.balanceOf(address(cdo)), cdoBalBefore + 99e6 - 100e6);
    // strategy minted 100 tokens backed by only 99 underlying
    assertEq(vault.balanceOf(address(cdo)), stratSupplyBefore + 100e6);
    assertEq(fot.balanceOf(address(vault)), 99e6); // before forwarding to borrower
}
```

Key assertions: `vault.balanceOf(cdo)` exceeds the underlying the strategy actually holds/forwarded by the fee amount, and the CDO's token balance drops by more than the tranche value credited to the attacker. Repeating the cycle accumulates unbacked strategy-token supply, which `finalizeDefaultRecovery`'s pro-rata `defaultRecoveryPrice` and funded-claim paths then socialize onto honest claimants.