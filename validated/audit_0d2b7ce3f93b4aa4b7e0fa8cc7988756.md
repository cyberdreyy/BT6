### Title
Blacklisted users cannot redirect credit-vault withdrawal claims to a non-blacklisted address - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` pays normal, loss-adjusted, defaulted, post-default and instant withdrawal claims only to the address-bound receipt owner `_user`. [1](#0-0) [2](#0-1)  For a USDC-denominated credit vault, a claim made after Circle blacklists the receipt owner reverts because USDC rejects transfers to blacklisted recipients. [3](#0-2)  The strategy receipt is non-transferable outside `idleCDO`, so the user cannot move the matured receipt to a clean address before claiming. [4](#0-3) 

### Finding Description
`requestWithdraw` burns the CDO-held principal and mints an equal receipt balance to `_user`; the same `_user` key later owns `withdrawsRequests`, APR0 accounting and `lastWithdrawRequest`. [5](#0-4)  `claimWithdrawRequest` accepts only `_user`, clears the request state and ultimately calls `_transferFundedClaim(_user, amount)`, which sends `underlyingToken` directly to that same address. [6](#0-5) [2](#0-1) 

The same destination restriction applies to instant claims through `claimInstantWithdrawRequest` and `_transferFundedClaim`. [7](#0-6)  Default-recovery claims have the same issue because `_transferDefaultRecovery` also sends only to `_user`. [8](#0-7) 

The vault accepts its payout currency through the unrestricted `initialize` parameter `_underlyingToken`, while the production-style Foundry fixture deploys the vault with mainnet USDC. [9](#0-8) [3](#0-2)  Therefore, a user can deposit and create a valid receipt while unblacklisted, be blacklisted before claiming, and have every subsequent claim revert at the final `safeTransfer`. [2](#0-1) 

### Impact Explanation
A matured withdrawal receipt can remain permanently unclaimable by its owner even though the underlying funds were funded and reserved. [6](#0-5)  This affects funded normal withdrawals, instant withdrawals, loss-adjusted withdrawals and default-recovery payouts denominated in blacklist-capable stablecoins. [10](#0-9) [8](#0-7) 

The impact is temporary freezing while blacklist status can later be removed, and effectively permanent freezing for an owner that remains blacklisted. [2](#0-1)  The owner has an emergency `transferToken` escape hatch, but paying the user through it does not clear the withdrawal receipt and therefore is not an accounting-safe withdrawal path. [11](#0-10) 

### Likelihood Explanation
The likelihood depends on the underlying token enforcing recipient blacklisting and on the user becoming blacklisted between request creation and claim. [9](#0-8)  The repository explicitly tests this credit-vault implementation against mainnet USDC, making the blacklist-capable payout token a supported configuration rather than a hypothetical token choice. [3](#0-2)  No protocol privileged actor, borrower default, pause, malformed epoch state or attacker action is required once the receipt has been funded. [2](#0-1) 

### Recommendation
Keep the receipt owner bound to the caller, but add a separate payout destination to the user-facing and strategy claim paths.

```solidity
function claimWithdrawRequest(address _user, address _receiver)
  external
  returns (uint256 amount)
{
  _onlyIdleCDO();
  require(_receiver != address(0), "Zero receiver");
  // Clear the same `_user` receipt state, but pass `_receiver`
  // to the underlying payout helper.
}
```

Correspondingly, expose `claimWithdrawRequest(address receiver)` and `claimInstantWithdrawRequest(address receiver)` on `IdleCDOEpochVariant`, while continuing to use `msg.sender` as the receipt owner. The claim should burn and clear only `msg.sender`’s receipt, then transfer the payout to the explicitly supplied receiver.

### Proof of Concept

```solidity
interface IUSDCBlacklist {
    function blacklister() external view returns (address);
    function blacklist(address account) external;
}

function testBlacklistedUserCannotRedirectFundedClaim() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address user = makeAddr("blacklisted-user");

    // Deposit and create a normal withdrawal receipt while the user is clean.
    deal(defaultUnderlying, user, amount, true);
    vm.startPrank(user);
    underlying.approve(address(cdoEpoch), amount);
    cdoEpoch.depositAA(amount);
    uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.stopPrank();

    // Fund the request through the ordinary epoch transition.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // The token issuer blacklists only the original receipt owner after funding.
    IUSDCBlacklist usdc = IUSDCBlacklist(defaultUnderlying);
    vm.prank(usdc.blacklister());
    usdc.blacklist(user);

    // There is no receiver parameter; payout is forced to the blacklisted owner.
    vm.prank(user);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();

    // The receipt remains outstanding after the failed payout.
    assertEq(
        IdleCreditVault(address(strategy)).withdrawsRequests(user),
        receipt
    );
}
```

The failed call occurs after the receipt has already been funded because `_claimFundedWithdrawRequest` reaches the terminal USDC transfer to `_user`. [12](#0-11) [2](#0-1)  An analogous PoC can blacklist the user before `claimInstantWithdrawRequest`, which uses the same forced `_user` payout helper. [7](#0-6)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L122-140)
```text
  /// @notice can be only called once
  /// @param _underlyingToken address of the underlying token (pool currency)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L909-917)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L963-965)
```text
  function transferToken(address _token, uint256 value, address _to) external onlyOwner {
    IERC20Detailed(_token).safeTransfer(_to, value);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L30-38)
```text
  uint256 internal constant FORK_BLOCK = 18678289;
  uint256 internal constant ONE_TRANCHE = 1e18;
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  string internal constant borrowerName = 'testBorrower';
  address internal manager = makeAddr('manager');
  address internal borrower = makeAddr('borrower');

  address internal defaultUnderlying = USDC;
  IdleCDOEpochVariant internal cdoEpoch;
```
