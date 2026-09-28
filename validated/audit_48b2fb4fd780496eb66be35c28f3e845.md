### Title
Fee-on-transfer deposits mint unbacked credit-vault shares - (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary

When the vault currency is a fee-on-transfer asset such as USDT with transfer fees enabled, `IdleCreditVault.deposit()` mints the nominal `_amount` of strategy tokens even though the strategy receives less underlying. Because those strategy tokens are treated as 1:1 underlying claims, the vault accumulates an unbacked liability. The shortfall is eventually borne by later withdrawal claimants or can prevent withdrawals altogether. [1](#0-0) 

### Finding Description

`IdleCDOCreditVault._deposit()` already measures the balance received from the depositor and mints only that net amount of tranche tokens. However, it then calls `IIdleCDOStrategy(strategy).deposit(_amount)` using the gross requested amount rather than the received amount. [2](#0-1) 

`IdleCreditVault.deposit()` performs another `safeTransferFrom` for the gross `_amount` and unconditionally mints `_amount` strategy tokens to the CDO without measuring the balance actually received. [3](#0-2) 

For a token charging a fee on either transfer, the accounting diverges:

1. User requests a deposit of `100`.
2. CDO receives `100 - fee` and mints tranches only for the received amount.
3. CDO sends the nominal `100` to `IdleCreditVault`.
4. `IdleCreditVault` receives less than `100` after its inbound transfer fee.
5. `IdleCreditVault` nevertheless mints `100` strategy tokens to the CDO.

The CDO counts strategy-token balance directly as underlying NAV because `getContractValue()` returns strategy-token balance plus raw-token balance minus unclaimed fees. [4](#0-3)  The strategy likewise exposes `price()` as a fixed `oneToken`, so every minted strategy token remains a nominal 1:1 underlying claim despite the missing fee amount. [5](#0-4) 

Withdrawals later burn receipt or strategy tokens and attempt to pay their nominal amount. Funded claims are paid through `_claimFundedWithdrawRequest()` and `_transferFundedClaim()`, while instant claims burn the receipt and transfer the nominal request amount. [6](#0-5) [7](#0-6) 

### Impact Explanation

This creates a direct backing deficit inside `IdleCreditVault`: aggregate strategy-token supply exceeds the underlying actually transferred into the vault by the cumulative inbound transfer fees. The deficit is not represented by `price()`, `getContractValue()`, or the strategy-token minting path, so normal NAV accounting treats the inflated claims as fully backed. [5](#0-4) [4](#0-3) 

When withdrawal or instant-withdrawal claims are funded, earlier claims can consume available underlying while later claims revert on transfer or become impaired by the missing amount. In a default or loss-allocation flow, the inflated strategy-token balance also increases the active-claim basis relative to assets actually delivered to the vault. [8](#0-7) [9](#0-8) 

An unprivileged KYC-passing lender can trigger the mismatch through an ordinary deposit. The attacker does not need a privileged protocol role; exploitation only requires the configured underlying to charge a transfer fee. Repeated deposits can scale the deficit proportionally to deposit volume and the token's configured fee rate.

### Likelihood Explanation

USDT is explicitly contemplated as a supported pool-style currency in the repository, and its transfer fee is a token-level configuration that can be enabled independently of this protocol. [10](#0-9) 

The vulnerable path is not guarded by a supported-token check, balance-delta check, skim, epoch gate, KYC check, or default check. The CDO already computes the correct received amount for depositor minting, but then discards that value when forwarding the gross `_amount` to the strategy. [2](#0-1) 

The issue can remain latent while borrower repayments or raw CDO liquidity cover the shortfall, but it deterministically makes strategy-token liabilities greater than delivered underlying whenever the second transfer charges a fee.

### Recommendation

Measure the actual amount received by `IdleCreditVault` and mint only that amount:

```solidity
uint256 before = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
uint256 received = underlyingToken.balanceOf(address(this)) - before;
_mint(msg.sender, received);
totEpochDeposits += received;
return received;
```

The CDO should also use the amount returned by `strategy.deposit()`, or calculate the strategy-token balance delta around the strategy deposit, rather than assuming the strategy minted the requested `_amount`. [2](#0-1) 

Other underlying pulls that establish recoverable claim basis, including `collectWithdrawFunds()`, `collectInstantWithdrawFunds()`, and `finalizeDefaultRecovery()`, should similarly account for actual received balances or explicitly reject fee-on-transfer tokens. [11](#0-10) [12](#0-11) 

### Proof of Concept

A Foundry mainnet-fork test can enable USDT's transfer fee through Tether's owner and then execute an ordinary CDO deposit:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

interface IUSDT {
    function setParams(uint256 newBasisPointsRate, uint256 newMaxFee) external;
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface ICreditCDO {
    function depositAA(uint256 amount) external returns (uint256);
    function strategyToken() external view returns (address);
}

interface IStrategyToken {
    function balanceOf(address account) external view returns (uint256);
}

contract FeeOnTransferCreditVaultPoC is Test {
    address constant USDT = 0xdAC17F958D2ee523a2206206994597C13D831ec7;
    address constant TETHER_OWNER =
        0xC6CDE7C39eB2f0F0095F41570af89eFC2C1Ea828;

    function testDepositMintsMoreStrategyTokensThanUnderlyingReceived()
        external
    {
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

        ICreditCDO cdo = ICreditCDO(<DEPLOYED_CDO>);
        IStrategyToken strategy = IStrategyToken(cdo.strategyToken());
        IUSDT usdt = IUSDT(USDT);

        // Configure a 1% fee on the fork. This is environment setup,
        // not a protocol-privileged attacker action.
        vm.prank(TETHER_OWNER);
        usdt.setParams(100, type(uint256).max);

        address lender = makeAddr("kycLender");
        uint256 amount = 100_000 * 1e6;
        deal(USDT, lender, amount);

        vm.startPrank(lender);
        usdt.approve(address(cdo), amount);
        cdo.depositAA(amount);
        vm.stopPrank();

        uint256 strategyShares = strategy.balanceOf(address(cdo));
        uint256 strategyUnderlying = usdt.balanceOf(address(strategy));

        assertEq(strategyShares, amount);
        assertLt(strategyUnderlying, strategyShares);
        assertEq(
            strategyShares - strategyUnderlying,
            amount / 100,
            "strategy minted nominal shares for a fee-reduced deposit"
        );
    }
}
```

The final assertions demonstrate the broken invariant directly: the CDO owns `amount` strategy tokens representing `amount` underlying at fixed `price()`, while the strategy received `amount * 99 / 100` underlying. Repeating the deposit increases the excess claim supply linearly, and later funded or instant claims are paid from an underlying balance smaller than their aggregate nominal claims.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L172-176)
```text
  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-314)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L316-350)
```text
  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L377-393)
```text
  /// @notice claim the instant withdraw request
  /// @dev we transfer the underlying tokens
  /// @param _user address of the user
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-429)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }

  /// @notice collect borrower-funded withdraw receipt funds
  /// @dev Only IdleCDO can call this function. When `_amount` is lower than the
  /// pending basis, the difference is a stopEpochWithDuration loss assigned to
  /// pending receipts and users later claim through `lossRecoveryPriceByEpoch`.
  /// Reverts if the resulting recovery price rounds to zero at `RECOVERY_FULL` precision.
  /// @param _amount number of funded tokens to collect
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L594-605)
```text
  /// @notice Get funds from IdleCDO and mint strategy tokens. Funds are not sent to the borrower here
  /// @param _amount number of underlyings to transfer
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L669-681)
```text
    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L706-709)
```text
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
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

**File:** contracts/strategies/mstable/IdleMStableStrategyWrapper.sol (L31-36)
```text
    /// @dev Must approve the underlying token before callings
    /// @notice Deposit one of the token supported by mstable and get BBtranche tokens
    /// @param token Address of the token to deposit (ex: DAI, USDC, USDT ...)
    /// @param _amount Amount of tokens to deposit
    /// @param minOutputQuantity Minimum number of mUSD token to receive on deposit
    function depositBBWithToken(
```
