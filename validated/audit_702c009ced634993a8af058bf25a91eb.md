### Title
Underlying-token blocklisting permanently freezes address-bound withdrawal claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCreditVault` pays matured withdrawal, instant-withdrawal, loss-adjusted, post-default, and default-recovery claims only to the address that owns the withdrawal receipt. If the configured underlying is a blocklistable token such as USDC and the receipt owner becomes blocked after creating the request, every payout path reverts. Unlike ordinary ERC20 positions, the user cannot move the claim to a clean address because strategy-token transfers are restricted to `idleCDO`, while the CDO exposes no receipt-migration or alternative-recipient claim function. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

`requestWithdraw` mints the strategy-token receipt directly to `_user` and records the withdrawal accounting under that same address. [4](#0-3) 

After the claim becomes funded, `IdleCDOEpochVariant.claimWithdrawRequest` passes `msg.sender` as `_user`, and `claimInstantWithdrawRequest` does the same. [3](#0-2) 

All payout paths ultimately call either `_transferFundedClaim(_user, amount)` or `_transferDefaultRecovery(_user, amount)`, both of which transfer the underlying directly to `_user`. Neither accepts a recipient parameter. [1](#0-0) 

The receipt cannot simply be moved to an unblocked address: `IdleCreditVault._transfer` reverts unless the caller is `idleCDO`, and `IdleCDOEpochVariant` does not expose a function that transfers a matured receipt to another claimant. [2](#0-1) 

This affects the full family of funded and recovery claims:

- normal funded claims through `_claimFundedWithdrawRequest`;
- loss-adjusted claims through `_claimLossAdjustedWithdrawRequest`;
- post-default claims through `_claimPostDefaultWithdrawRequest`;
- defaulted normal claims through `_claimDefaultedWithdrawRequest`;
- defaulted instant claims through `_claimDefaultedInstantWithdrawRequest`;
- funded instant claims through `claimInstantWithdrawRequest`.

Each of those paths burns or clears the blocked user's receipt and then sends the underlying to that same blocked address. [5](#0-4) [6](#0-5) [7](#0-6) 

The project already supports configurable underlying tokens through `token` and `underlyingToken`, so this is not limited to a hypothetical asset: a deployment using USDC or another blocklistable pool currency inherits the failure mode. [8](#0-7) 

### Impact Explanation

A user whose address is added to the underlying token's blocklist after creating a withdrawal request can permanently lose access to the entire funded claim. The revert does not only delay payment: the blocked account cannot specify another recipient and cannot transfer the non-transferable receipt to another address. [9](#0-8) [2](#0-1) 

For a claim of `X` underlying tokens, `X` remains locked in `IdleCreditVault` or remains accounted as unavailable reserve while the receipt is address-bound to the blocked user. In the recovery paths, `_transferDefaultRecovery` decreases `defaultRecoveryReserve` only inside the successful call; therefore the transfer revert preserves the accounting but leaves the user's claim unspendable. [10](#0-9) 

This breaks the invariant that one funded receipt corresponds to one claimable payout. The receipt remains economically backed, but no transaction can deliver its backing while the receipt owner is blocked. [11](#0-10) 

### Likelihood Explanation

The issue requires two conditions:

1. the vault uses a blocklistable underlying such as USDC;
2. a withdrawal receipt holder is added to that token's blocklist while holding an unclaimed receipt.

The second condition can occur after the user has already requested withdrawal, so pre-request wallet screening or transferring tranche tokens before requesting does not help. The affected receipt is minted to the user's address at request time and then becomes non-transferable outside CDO-mediated movement. [4](#0-3) [2](#0-1) 

Likelihood is deployment- and user-dependent, but impact is deterministic once the conditions occur. The flaw applies to ordinary funded withdrawals, instant withdrawals, partial-loss receipts, and all default-recovery payouts.

### Recommendation

Allow the receipt owner to nominate a claim recipient while preserving the existing owner-based accounting and receipt burn.

For example:

```diff
// contracts/IdleCDOEpochVariant.sol

-function claimWithdrawRequest() external {
-    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
+function claimWithdrawRequest(address recipient) external {
+    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender, recipient);
 }

-function claimInstantWithdrawRequest() external {
+function claimInstantWithdrawRequest(address recipient) external {
     _checkNotAllowed(!allowInstantWithdraw);
-    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
+    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender, recipient);
 }
```

```diff
// contracts/strategies/idle/IdleCreditVault.sol

-function claimWithdrawRequest(address _user) external returns (uint256 amount) {
+function claimWithdrawRequest(address _user, address _recipient) external returns (uint256 amount) {
     _onlyIdleCDO();
+    if (_recipient == address(0)) revert NotAllowed();
     ...
-    _transferFundedClaim(_user, amount);
+    _transferFundedClaim(_recipient, amount);
 }
```

All internal claim helpers should similarly separate:

- `_user`: owner whose accounting and receipt tokens are cleared or burned;
- `_recipient`: underlying payout destination.

The existing `DefaultDistributor.claim(address _to)` pattern already separates the tranche owner, `msg.sender`, from the recipient, which is the appropriate authorization model for this issue. [12](#0-11) 

### Proof of Concept

A Foundry fork test can reproduce the freeze with real USDC:

```solidity
// test/foundry/BlocklistedClaim.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

interface IBlocklistableToken {
    function blacklister() external view returns (address);
    function blacklist(address account) external;
    function isBlacklisted(address account) external view returns (bool);
}

contract BlocklistedClaimTest is Test {
    // Configure the fork for a deployed vault whose underlyingToken is USDC.
    IdleCDOEpochVariant cdo;
    IdleCreditVault vault;
    IERC20Detailed usdc;

    address user = makeAddr("blocked-withdrawer");

    function testBlockedUserCannotClaimOrMoveReceipt() external {
        // Preconditions from the existing deployment/fixture:
        // 1. user holds an AA or BB tranche balance.
        // 2. withdrawal requests are enabled.
        // 3. usdc == address(vault.underlyingToken()).

        vm.startPrank(user);
        uint256 requested = cdo.requestWithdraw(0, cdo.AATranche());
        vm.stopPrank();
        assertGt(requested, 0);

        // Fund the request by stopping the current epoch with full repayment.
        // Existing test helpers can perform the borrower funding and stopEpoch flow.
        // Afterward, vault holds enough USDC to pay the request.

        // Real USDC exposes blacklister() and blacklist(address).
        address blacklister = IBlocklistableToken(address(usdc)).blacklister();
        vm.prank(blacklister);
        IBlocklistableToken(address(usdc)).blacklist(user);
        assertTrue(IBlocklistableToken(address(usdc)).isBlacklisted(user));

        uint256 vaultBalanceBefore = usdc.balanceOf(address(vault));

        vm.prank(user);
        vm.expectRevert();
        cdo.claimWithdrawRequest();

        assertEq(
            usdc.balanceOf(address(vault)),
            vaultBalanceBefore,
            "claim backing remains locked after payout reverts"
        );

        // The user cannot migrate the strategy-token receipt to a clean address.
        address cleanRecipient = makeAddr("clean-recipient");
        vm.prank(user);
        vm.expectRevert(NotAllowed.selector);
        vault.transfer(cleanRecipient, requested);
    }
}
```

The same pattern applies after `finalizeDefault`: a defaulted receipt can be cleared only through `claimWithdrawRequest`, but `_transferDefaultRecovery` still forces the payout to the blocked receipt owner. [13](#0-12)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L38-45)
```text
  /// @notice underlying token address (pool currency for Clearpool)
  address public override token;
  /// @notice decimals of the underlying asset
  uint256 public override tokenDecimals;
  /// @notice one underlying token
  uint256 public override oneToken;
  /// @notice underlying ERC20 token contract (pool currency for Clearpool)
  IERC20Detailed public underlyingToken;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-313)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-350)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-800)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }

  /// @notice Claim a defaulted normal withdraw receipt with the finalized recovery haircut.
  /// @param _user address of the user
  /// @return amount amount paid from default recovery reserve
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }

  /// @notice Claim a stopEpochWithDuration loss-adjusted withdraw receipt.
  /// @param _user address of the user
  /// @return amount amount paid from funded strategy underlyings
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L839-855)
```text
  /// @notice Claim a defaulted instant-withdraw receipt with the finalized recovery haircut.
  /// @param _user address of the user
  /// @return claimBasis amount of instant-withdraw basis cleared
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-917)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }

  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L937-941)
```text
  /// @inheritdoc ERC20Upgradeable
  /// @dev Receipt claims are address-bound, so only the IdleCDO can move strategy tokens.
  function _transfer(address sender, address recipient, uint256 amount) internal virtual override {
    if (msg.sender != idleCDO) revert NotAllowed();
    super._transfer(sender, recipient, amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L965-979)
```text
  /// @notice Claim a withdraw request from the vault. Can be done when at least 1 epoch passed
  /// since last withdraw request
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
  }

  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```

**File:** contracts/DefaultDistributor.sol (L32-40)
```text
  /// @notice transfer tranche tokens to this contract and send proportional amount of 
  /// underlying to `_to`
  /// @param _to recipient address
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
```
