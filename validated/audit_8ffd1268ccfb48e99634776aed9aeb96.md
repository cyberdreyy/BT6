### Title
Strategy mints 1:1 strategy tokens for requested amount while receiving less underlying on fee-on-transfer tokens - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.deposit` pulls exactly `_amount` underlying from the `IdleCDO` and mints `_amount` strategy tokens, assuming the strategy actually receives `_amount` tokens. The upstream `IdleCDOCreditVault._deposit` correctly mints tranche shares based on the *actual* amount received from the user (`post - pre` balance), but it then forwards the *requested* `_amount` to `strategy.deposit(_amount)`. On a fee-on-transfer or deflationary underlying, each hop delivers less than the accounted amount, inflating the strategy-token supply relative to real underlying held.

### Finding Description
Two stacked assumptions break the 1:1 backing invariant of `IdleCreditVault`:

1. `IdleCDOCreditVault._deposit` mints tranche shares using the actually received amount (`_contractTokenBalance(_token) - _preBal`) but calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the full requested amount, not the received amount (`contracts/IdleCDOCreditVault.sol:203-211`).

2. `IdleCreditVault.deposit` executes `underlyingToken.safeTransferFrom(msg.sender, address(this), _amount)` and unconditionally mints `_amount` strategy tokens to the CDO (`contracts/strategies/idle/IdleCreditVault.sol:596-617`). The balance actually received is never measured.

With a transfer-on-fee token at fee `f`:
- The CDO receives `amount * (1 - f)` from the user but tries to forward `amount` to the strategy. The deposit only succeeds if the CDO holds a surplus (unsolicited transfers, leftover buffer liquidity). If not, it reverts.
- When it succeeds, the strategy again receives only `amount * (1 - f)` but mints `amount` strategy tokens to the CDO at `price() == oneToken` (always 1:1, `IdleCreditVault.sol:172-176`).

The same unchecked-amount pattern exists elsewhere on the same surface: `collectWithdrawFunds`/`collectInstantWithdrawFunds` pull a fixed `_amount` from the CDO (`IdleCreditVault.sol:398-430`), and `finalizeDefaultRecovery` pulls `_recoveredAmount` (`IdleCreditVault.sol:706-709`), crediting the full amount to `defaultRecoveryReserve` regardless of what actually arrived.

### Impact Explanation
Strategy tokens are claims on underlying at a fixed 1:1 price. Minting `_amount` tokens while holding `_amount * (1-f)` underlying makes the strategy structurally insolvent by the cumulative fee amount. All payout paths — `_transferFundedClaim`, `_transferDefaultRecovery` (`IdleCreditVault.sol:897-917`) — pay claimants the full recorded amount until the strategy's underlying is exhausted. Early claimants are paid in full; the shortfall is socialized onto the last claimants, whose receipts become permanently unclaimable. A depositor (any KYC-passing lender or, more realistically, the honest borrower/manager flows which seed CDO surplus) can mint fully-priced tranche shares while delivering less real value, extracting the difference from other tranche holders at withdrawal time.

### Likelihood Explanation
Requires the vault to be initialized with a fee-on-transfer/deflationary underlying (e.g., USDT with fees enabled, or similar stablecoin used as pool currency). `IdleCDOCreditVault.initialize` accepts any ERC20 as `token` with no check (`contracts/IdleCDOCreditVault.sol:44-86`), so this is a deployment-configuration exposure rather than something attackers can inject. Where such a token is used, every deposit and every recovery pull silently inflates accounting, making insolvency deterministic rather than incidental.

### Recommendation
Measure actual received amounts at each hop:
- In `IdleCDOCreditVault._deposit`, pass the received amount (`_contractTokenBalance(_token) - _preBal`) to `strategy.deposit(...)`, not `_amount`.
- In `IdleCreditVault.deposit`, compute `received = underlyingToken.balanceOf(this) - balBefore` after `safeTransferFrom` and mint `received` strategy tokens; apply the same pattern in `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery`.
- Alternatively, revert in `initialize` if the token is known to charge transfer fees.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract FeeToken is ERC20 {
    uint256 public feeBps = 100; // 1%
    function _transfer(address s, address r, uint256 a) internal override {
        uint256 fee = a * feeBps / 10_000;
        super._transfer(s, r, a - fee);
        if (fee > 0) super._transfer(s, address(0xdead), fee);
    }
}

contract FeeOnTransferInsolvencyTest is Test {
    IdleCDOCreditVault cdo;
    IdleCreditVault strat;
    FeeToken tok;

    function test_InflatedStrategyTokens() public {
        tok = new FeeToken();
        // deploy + initialize cdo/strategy with tok as underlying (owner, manager, borrower honest)
        // 1. Seed CDO with a donated surplus so the strategy pull of _amount succeeds
        //    (mimics skimmed donations / leftover buffer liquidity).
        tok.transfer(address(cdo), 100e18); // CDO holds dust
        // 2. Alice (KYC'd lender) deposits 10_000e18.
        //    CDO receives 9_900e18, mints Alice 9_900 shares (correct),
        //    then strategy.deposit(10_000e18) pulls the full 10_000
        //    (using Alice's 9_900 + 100 donated dust).
        //    Strategy receives 9_900 but mints 10_000 strategy tokens to the CDO.
        assertEq(strat.balanceOf(address(cdo)), 10_000e18);      // minted at par
        assertEq(tok.balanceOf(address(strat)), 9_900e18);       // actual backing
        // 3. Claims pay 1:1: the first 9_900e18 of claims succeed,
        //    the final 100e18 of receipts are permanently unpayable.
    }
}
```
On a fork test, deploy `IdleCDOCreditVault` + `IdleCreditVault` proxies against a mainnet fee-enabled token (or a mock fee wrapper), seed the CDO with any positive underlying balance, deposit, and assert `strategy.balanceOf(cdo) > underlying.balanceOf(strategy)`, then show the last `claimWithdrawRequest` reverts on insufficient balance.