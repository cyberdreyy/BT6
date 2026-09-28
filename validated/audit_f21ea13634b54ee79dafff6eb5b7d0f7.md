### Title
Fee-on-transfer deposits mint unbacked tranche and strategy tokens - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Mid-epoch deposits credit the gross `_amount` even though a fee-on-transfer underlying delivers only the net amount to the borrower. `IdleCreditVault.mintStrategyTokens` then mints strategy tokens for the full gross amount, inflating CDO NAV by the transfer fee. [1](#0-0)  Because credit-vault strategy tokens are treated as 1:1 underlying for NAV, the shortfall becomes an unbacked claim against the pool. [2](#0-1) 

### Finding Description
Ordinary deposits correctly measure the CDO’s balance before and after `_transferUnderlyingsFrom`, minting tranche tokens only for the amount actually received. [3](#0-2)  The dedicated running-epoch deposit path sends the user’s underlying directly toward the borrower and credits/mints strategy-token backing using the requested gross amount rather than the borrower balance delta. `mintStrategyTokens(_amount)` trusts that caller-supplied amount and mints the same number of strategy tokens without verifying that `_amount` was actually received. [1](#0-0) 

For an underlying charging fee `f`, depositing `A` therefore produces:

```text
borrower receives: A - f
strategy tokens minted to CDO: A
CDO NAV increase: A
actual external backing increase: A - f
unbacked NAV: f
```

`getContractValue()` counts the CDO’s strategy-token balance directly, so the missing `f` is still included in NAV. [2](#0-1)  The resulting inflation is propagated through `_updateAccounting()` into tranche prices and `lastNAVAA`/`lastNAVBB`. [4](#0-3) 

### Impact Explanation
Each fee-on-transfer mid-epoch deposit creates an unbacked NAV equal to the transfer fee. The depositor receives tranche shares based on the gross deposit while the borrower only receives the net amount, so existing and subsequent holders are exposed to a shortfall when principal is recalled or withdrawals are funded. Repeated deposits can accumulate the deficit, and the final withdrawers may be unable to receive the full value represented by their tranche tokens. This breaks the solvency and fair-mint invariants: circulating tranche claims and strategy tokens exceed recoverable underlying. [2](#0-1) 

### Likelihood Explanation
The issue requires the vault underlying to be a fee-on-transfer token and the operator-enabled mid-epoch deposit path to be available. No privileged misconduct is needed: a KYC-passing lender can call `depositDuringEpoch` normally. Other transfer paths already show the correct mitigation pattern by recording the balance delta after token movement. [3](#0-2) 

### Recommendation
Measure the amount actually delivered before crediting the deposit. For a direct-to-borrower deposit, either require the borrower to acknowledge the received net amount or route the token through the CDO/strategy and calculate `balanceAfter - balanceBefore`. Pass only that received amount to `_mintSharesAtCurrPrice`, NAV accounting, expected-interest accounting, and `mintStrategyTokens`. Reject fee-on-transfer underlying tokens explicitly if net-received accounting cannot be made reliable. [5](#0-4) 

### Proof of Concept
The following Foundry test demonstrates the accounting invariant using a 1% fee-on-transfer token. The exact deployment/setup helpers should be adapted to the existing `IdleCreditVault.t.sol` fixtures. [6](#0-5) 

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";

contract FoTToken is ERC20 {
    uint256 public constant FEE_BPS = 100;

    constructor() ERC20("Fee Token", "FEE") {
        _mint(msg.sender, 1_000_000e18);
    }

    function _transfer(
        address from,
        address to,
        uint256 amount
    ) internal override {
        uint256 fee = amount * FEE_BPS / 10_000;
        super._transfer(from, to, amount - fee);
        super._transfer(from, address(0xdead), fee);
    }
}

contract FeeOnTransferDepositTest is Test {
    FoTToken internal fot;
    IdleCDOEpochVariant internal cdo;
    IdleCreditVault internal strategy;
    address internal borrower;
    address internal lender = address(0xA11CE);

    function setUp() public {
        fot = new FoTToken();

        /*
         * Deploy/initialize IdleCreditVault and IdleCDOEpochVariant using
         * address(fot) as `token`, configure `borrower`, start an epoch,
         * and enable `isDepositDuringEpochDisabled == false`.
         *
         * The resulting objects must satisfy:
         * cdo.token() == address(fot)
         * cdo.strategy() == address(strategy)
         * cdo.isEpochRunning() == true
         */
    }

    function testMidEpochFoTDepositInflatesNav() public {
        uint256 amount = 100_000e18;
        uint256 expectedReceived = amount * 9_900 / 10_000;

        deal(address(fot), lender, amount);

        uint256 borrowerBefore = fot.balanceOf(borrower);
        uint256 navBefore = cdo.getContractValue();
        uint256 strategyBefore = fot.balanceOf(address(strategy));

        vm.startPrank(lender);
        fot.approve(address(cdo), amount);
        cdo.depositDuringEpoch(amount, cdo.BBTranche());
        vm.stopPrank();

        assertEq(
            fot.balanceOf(borrower) - borrowerBefore,
            expectedReceived,
            "borrower receives net amount"
        );

        uint256 strategyMinted = IERC20(address(strategy)).balanceOf(address(cdo));

        assertEq(
            strategyMinted,
            amount,
            "strategy tokens are minted for the gross amount"
        );

        assertEq(
            cdo.getContractValue() - navBefore,
            amount,
            "NAV credits the gross amount"
        );

        assertEq(
            fot.balanceOf(address(strategy)) - strategyBefore,
            0,
            "direct borrower deposit adds no strategy-held backing"
        );

        assertGt(
            cdo.getContractValue() - navBefore,
            fot.balanceOf(borrower) - borrowerBefore,
            "unbacked NAV equals the transfer fee"
        );
    }
}
```

Expected result: the borrower receives `99,000e18`, while the CDO receives `100,000e18` strategy tokens and NAV increases by `100,000e18`, leaving `1,000e18` unbacked. [2](#0-1)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L619-624)
```text
  /// @notice Mint strategy tokens to the CDO without moving underlyings
  /// @dev Used for mid-epoch deposits that send funds directly to the borrower
  function mintStrategyTokens(uint256 _amount) external {
    _onlyIdleCDO();
    _mint(msg.sender, _amount);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L123-128)
```text
  /// @notice calculates the current net TVL (in `token` terms)
  /// @dev `unclaimedFees` are not counted.
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L201-212)
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
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L222-236)
```text
  function _updateAccounting() internal virtual returns (bool shutdown) {
    _accrueManagementFee();
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;
    uint256 _lastNAV = _lastNAVAA + _lastNAVBB;
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);
```

**File:** test/foundry/IdleCreditVault.t.sol (L1933-1967)
```text
  function testDepositDuringEpochMintsDiscountedSharesBB() external {
    uint256 amountAA = 10000 * ONE_SCALE;
    uint256 amountBB = 10000 * ONE_SCALE;

    idleCDO.depositAA(amountAA);
    idleCDO.depositBB(amountBB);
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() - (cdoEpoch.epochDuration() / 2));

    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address user = makeAddr('midEpochBB');
    uint256 depositAmount = 1000 * ONE_SCALE;
    deal(defaultUnderlying, user, depositAmount);

    MidEpochDepositExpectations memory exp = _calcMidEpochDepositExpectations(address(BBtranche), depositAmount);
    uint256 borrowerBal = IERC20Detailed(defaultUnderlying).balanceOf(borrower);
    uint256 strategyTokenBal = IERC20Detailed(address(strategy)).balanceOf(address(cdoEpoch));

    vm.startPrank(user);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), depositAmount);
    uint256 minted = cdoEpoch.depositDuringEpoch(depositAmount, address(BBtranche));
    vm.stopPrank();

    assertEq(minted, exp.expectedMinted, 'minted is wrong');
    assertEq(IERC20(address(BBtranche)).balanceOf(user), minted, 'user tranche balance is wrong');
    assertEq(cdoEpoch.expectedEpochInterest(), exp.expectedInterest + exp.interest, 'expectedEpochInterest is wrong');
    assertEq(cdoEpoch.lastNAVBB(), exp.lastNAVTranche + depositAmount, 'lastNAVBB is wrong');
    assertEq(IERC20Detailed(defaultUnderlying).balanceOf(borrower) - borrowerBal, depositAmount, 'borrower funds are wrong');
    assertEq(IERC20Detailed(address(strategy)).balanceOf(address(cdoEpoch)) - strategyTokenBal, depositAmount, 'strategy token balance is wrong');
```
