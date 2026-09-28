### Title
Fee-on-transfer deposits create unbacked strategy tokens and strand withdrawals - (File: contracts/IdleCDOCreditVault.sol)

### Summary
`IdleCDOCreditVault._deposit` correctly mints tranche tokens from the net amount received from the lender, but then forwards the gross `_amount` to `IdleCreditVault.deposit`. With a fee-on-transfer underlying, this causes two fees while accounting recognizes only the first one.

### Finding Description
During deposit, the CDO measures its balance before and after pulling `_amount`, and mints tranche shares only for `received = balanceAfter - balanceBefore` [1](#0-0) . It nevertheless calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the original gross amount [2](#0-1) .

`IdleCreditVault.deposit` then pulls `_amount` from the CDO and mints `_amount` of 1:1 strategy tokens without measuring the amount actually received [3](#0-2) . Since `IdleCreditVault.price()` is fixed at `oneToken`, those minted strategy tokens are treated as fully backed underlying [4](#0-3) .

For a token charging a fee `f` on each transfer:

1. Lender transfer to CDO: CDO receives `_amount - f`.
2. CDO transfer to strategy: CDO is debited `_amount`; strategy receives `_amount - f`.
3. Strategy mints `_amount` strategy tokens.
4. Net vault assets increase by `_amount - 2f`, while claim basis increases by `_amount - f`.
5. The remaining `f` is an immediate insolvency against existing liquidity, and the deposit reverts if no existing CDO liquidity can cover the second gross pull.

### Impact Explanation
Each successful deposit permanently leaves underlying backing below recorded claims by one transfer fee. A lender can repeatedly deposit through this path and socialize the second fee across the pool. The deficit accumulates until a later withdrawal, queued claim, epoch stop, or liquidation cannot obtain the full accounted amount, producing a permanent shortfall for remaining tranche holders or withdraw requesters.

If the CDO has no spare underlying balance, the same mismatch instead makes deposits revert immediately because the strategy attempts to pull more underlying than the CDO actually received [5](#0-4) .

### Likelihood Explanation
The vault accepts the underlying configured during initialization and does not restrict it to non-fee tokens [6](#0-5) . The issue is conditional on using, or later upgrading to, a token that deducts value on `transferFrom`, such as a fee-enabled stablecoin or fee-bearing asset.

### Recommendation
Pass only the received amount to the strategy and make the strategy account for its own actual receipt:

```solidity
uint256 received = _contractTokenBalance(_token) - _preBal;
_minted = _mintSharesAtCurrPrice(received, msg.sender, _tranche);
IIdleCDOStrategy(strategy).deposit(received);
```

In `IdleCreditVault.deposit`, also compute `received = balanceAfter - balanceBefore` around `safeTransferFrom` and mint strategy tokens for that received amount rather than the requested `_amount`.

### Proof of Concept
A Foundry fork test can deploy `IdleCreditVault` with a fee-on-transfer underlying, wire it as the CDO strategy, seed the CDO with pre-existing underlying liquidity so the gross strategy pull succeeds, then call `depositAA`/`depositBB`.

For `_amount = 100` and a 1% transfer fee:

```solidity
uint256 cdoBefore = token.balanceOf(address(cdo));
uint256 strategyBefore = token.balanceOf(address(strategy));
uint256 claimsBefore = strategy.balanceOf(address(cdo));

vm.prank(lender);
cdo.depositAA(100);

// Actual underlying gained by the whole system is 98:
assertEq(
    token.balanceOf(address(cdo)) + token.balanceOf(address(strategy))
        - cdoBefore - strategyBefore,
    98
);

// Strategy-token claims gained by the CDO are 100:
assertEq(strategy.balanceOf(address(cdo)) - claimsBefore, 100);

// Depositor receives shares for only 99, but existing holders absorb the
// additional missing token created by the second transfer fee.
uint256 shares = AATranche(cdo.AATranche()).balanceOf(lender);
assertEq(shares, 99);
```

With `cdoBefore == 0`, replace the assertions with `vm.expectRevert()` because `IdleCreditVault.deposit` attempts to pull `100` while the CDO received only `99` [7](#0-6) .

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L172-175)
```text
  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
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
