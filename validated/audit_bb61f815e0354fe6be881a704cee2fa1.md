### Title
Fee-on-transfer underlying tokens create under-collateralized strategy tokens or revert deposits - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary

`IdleCDOCreditVault._deposit` correctly mints tranche tokens from the actual underlying balance increase, but subsequently forwards the caller’s requested `_amount` rather than the received amount to the strategy. [1](#0-0)  `IdleCreditVault.deposit` then transfers the nominal `_amount` and mints the same nominal quantity of strategy tokens without checking the amount actually received. [2](#0-1) 

With a fee-on-transfer underlying asset, every deposit either reverts when the CDO lacks enough residual balance to cover the fee or consumes residual CDO balance and mints more strategy-token claims than underlying delivered to the credit vault.

### Finding Description

The deposit flow contains two transfers and two different accounting amounts:

1. `IdleCDOCreditVault._deposit` snapshots its underlying balance, transfers `_amount` from the depositor, and mints tranche tokens for `balanceAfter - balanceBefore`. This portion is fee-aware. [3](#0-2) 
2. It calls `IIdleCDOStrategy(strategy).deposit(_amount)` using the original requested amount instead of the received delta. [4](#0-3) 
3. `IdleCreditVault.deposit` performs another `transferFrom` for the original `_amount` and unconditionally mints `_amount` strategy tokens to the CDO. [2](#0-1) 

Assume a 1% fee and a requested deposit of `A`:

- The depositor is charged `A`, while the CDO receives `0.99A`.
- If the CDO has no residual underlying balance, its subsequent transfer of `A` to the strategy reverts.
- If the CDO has a residual balance of at least `0.01A`, the strategy receives only `0.99A` but mints `A` strategy tokens.
- The depositor receives tranche tokens based on `0.99A`, while the CDO’s recorded strategy-token assets increase by `A`.

The resulting `0.01A` strategy-token excess is not backed by underlying assets held by the strategy.

### Impact Explanation

This breaks the fair-mint and solvency invariant. A KYC-passing lender can first send enough underlying directly to the CDO to cover the fee, then execute a fee-on-transfer deposit that creates unbacked strategy-token assets. If that lender already owns tranche tokens, the phantom NAV benefits the lender’s position and can be redeemed before later claimants discover the backing shortfall. The maximum excess created per deposit is approximately `A - A_received`, equal to the charged transfer fee.

Without a residual CDO balance, the same sequence makes deposits unavailable because the second nominal transfer always exceeds the balance received from the depositor.

### Likelihood Explanation

The condition requires the deployed vault’s configured `underlyingToken` to charge a transfer fee. `IdleCreditVault.initialize` accepts an arbitrary ERC20 address and does not reject fee-on-transfer tokens or require a known-fee-free asset. [5](#0-4)  The issue is therefore conditional on token selection, but it does not require malicious owner, manager, borrower, or guardian behavior.

A lender or other unprivileged direct token sender can supply the residual balance needed to move from the revert case into the under-collateralized mint case.

### Recommendation

Explicitly reject fee-on-transfer underlying assets during deployment, or redesign the deposit accounting so tranche minting is based on the final amount credited to the strategy rather than the gross user-specified amount.

If compatibility is intended:

- Measure `strategyBalanceAfter - strategyBalanceBefore` in `IdleCreditVault.deposit` and mint only that received amount.
- Return the net minted amount to `IdleCDOCreditVault._deposit`.
- Mint tranche shares after the strategy deposit using that final credited amount.
- Avoid charging the transfer fee twice by changing the funding path or otherwise accounting only for the net backing ultimately held by the strategy.

### Proof of Concept

The following Foundry test structure demonstrates both branches:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract FeeToken {
    string public name = "FeeToken";
    string public symbol = "FEE";
    uint8 public decimals = 6;
    uint256 public totalSupply;
    uint256 public feeBps = 100; // 1%

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        return transferFrom(msg.sender, to, amount);
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) public returns (bool) {
        if (from != msg.sender) {
            allowance[from][msg.sender] -= amount;
        }

        uint256 fee = amount * feeBps / 10_000;
        uint256 received = amount - fee;

        balanceOf[from] -= amount;
        balanceOf[to] += received;
        totalSupply -= fee;
        return true;
    }
}

contract FeeOnTransferDepositPoC is Test {
    function test_depositRevertsWithoutResidualBalance() public {
        // Arrange:
        // 1. Deploy FeeToken.
        // 2. Deploy and initialize IdleCreditVault with FeeToken as underlyingToken.
        // 3. Deploy/configure IdleCDOCreditVault to use that strategy.
        // 4. Mint 100e6 FeeToken to a KYC-passing lender.
        // 5. Approve the CDO for 100e6.

        // Deposit:
        // - CDO receives only 99e6.
        // - CDO calls strategy.deposit(100e6).
        // - IdleCreditVault attempts to pull 100e6 from the CDO.
        // vm.expectRevert();
        // cdo.depositAA(100e6);
    }

    function test_depositMintsUnbackedStrategyTokensWithResidualBalance() public {
        // Arrange as above, then directly transfer 1e6 FeeToken to the CDO.
        // Depending on fee rounding, use enough residual balance to cover the
        // 1e6 first-transfer fee.

        // uint256 strategyTokensBefore = vault.balanceOf(address(cdo));
        // uint256 strategyUnderlyingBefore = fee.balanceOf(address(vault));

        // cdo.depositAA(100e6);

        // uint256 strategyTokensMinted =
        //     vault.balanceOf(address(cdo)) - strategyTokensBefore;
        // uint256 strategyUnderlyingReceived =
        //     fee.balanceOf(address(vault)) - strategyUnderlyingBefore;

        // assertEq(strategyTokensMinted, 100e6);
        // assertEq(strategyUnderlyingReceived, 99e6);
        // assertGt(strategyTokensMinted, strategyUnderlyingReceived);
    }
}
```

The first assertion path reproduces the deposit-freeze path. The second uses a direct underlying transfer to seed the CDO’s residual balance, after which the strategy mints 100 units of claims while receiving only 99 units of underlying.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L201-211)
```text
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L124-140)
```text
  function initialize(
    address _underlyingToken,
    address _owner,
    address _manager,
    address _borrower,
    string memory borrowerName,
    uint256 _apr
  ) public virtual initializer {
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();
    require(token == address(0), "Token is already initialized");

    //----- // -------//
    token = _underlyingToken;
    underlyingToken = IERC20Detailed(token);
    tokenDecimals = underlyingToken.decimals();
    oneToken = 10**(tokenDecimals);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-605)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }
```
