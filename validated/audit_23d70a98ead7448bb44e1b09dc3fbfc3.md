### Title
Withdraw and deposit receipts are permanently frozen for beneficiaries that cannot call `claim*()` - (File: contracts/IdleCDOEpochQueue.sol / contracts/strategies/idle/IdleCreditVault.sol)

### Summary
All queued-withdraw, deposit and vault withdraw receipts in the epoch system are address-bound to `msg.sender` and non-transferable. The only payout paths — `IdleCDOEpochQueue.claimWithdrawRequest(_epoch)` / `claimDepositRequest(_epoch)`, and `IdleCDOEpochVariant.claimWithdrawRequest()` / `claimInstantWithdrawRequest()` → `IdleCreditVault.claimWithdrawRequest(_user)` — key the claim on the caller. If the beneficiary is a contract that cannot invoke these functions (e.g., an immutable vault/router/integration contract that deposited or requested a withdraw), the underlying is locked forever; the only escape is privileged owner rescue (`transferToken`), mirroring the reported `TimeLock` bug.

### Finding Description
- `IdleCDOEpochQueue.claimWithdrawRequest` reads `userWithdrawalsEpochs[msg.sender][_epoch]` and pays `msg.sender` [1](#0-0) . `claimDepositRequest` does the same for `userDepositsEpochs[msg.sender]` [2](#0-1) . There is no `for`/`onBehalfOf` parameter and no way to delegate the claim.
- `IdleCDOEpochVariant.claimWithdrawRequest()` forwards `msg.sender` as `_user` to `IdleCreditVault.claimWithdrawRequest(_user)`, which burns the receipt and calls `_transferFundedClaim(_user, amount)` — paying only the recorded user [3](#0-2) [4](#0-3) .
- The receipt position cannot escape the beneficiary address: strategy-token transfers are disabled for everyone except `idleCDO` (`_transfer` reverts unless `msg.sender == idleCDO`, and `setCanTransfer` can only clear the flag, never set it) [5](#0-4) . The queue's `userWithdrawalsEpochs`/`userDepositsEpochs` entitlements are not tokenized at all.
- A contract beneficiary that requested a withdraw during the buffer phase (or deposited and never claimed tranche tokens) therefore has no transaction it can make that unlocks its funds. The underlyings sit in `IdleCDOEpochQueue`/`IdleCreditVault` until `transferToken` owner rescue [6](#0-5) .

### Impact Explanation
Permanent freezing of funds. Any lender contract (e.g., an ERC4626 wrapper, multisig-style smart wallet lacking an arbitrary-call path, or an integration contract) that deposits via the queue or calls `requestWithdraw` on the CDO and later cannot execute `claimWithdrawRequest` loses access to 100% of its entitlement. The same applies to `DefaultDistributor.claim`, which pulls tranche tokens from `msg.sender` [7](#0-6)  — a contract holder of tranche tokens that cannot self-call cannot recover its share of default proceeds.

### Likelihood Explanation
No guard mitigates this: `isWalletAllowed`/Keyring KYC only gates entry, not claim-ability; `_onlyIdleCDO` and the epoch gating check timing, not caller type. Funds are recoverable only through the honest `onlyOwner` `transferToken` rescue, so the loss is real but contingent on a beneficiary contract lacking a generic `call`/`execute` function — an integration-pattern risk rather than something an attacker can force. Medium likelihood, permanent impact on affected funds.

### Recommendation
Allow claims to specify a beneficiary distinct from the receipt owner only by the owner itself — i.e., the safer fix is the inverse of the naive report: keep claims `msg.sender`-keyed but make entitlements transferable. Options:
- Re-enable a controlled transfer of queue entitlements (`userWithdrawalsEpochs`/`userDepositsEpochs`) or of the vault receipt tokens so a frozen contract can migrate its claim to an operable address.
- Add `claimWithdrawRequest(uint256 _epoch, address _to)` on the queue and `claimWithdrawRequest(address _to)` on the CDO, where the payout goes to `_to` but the receipt debited is still `msg.sender`'s — this lets a contract pay out to an EOA it controls even if it cannot receive-push safely. (Does not help a contract that cannot call at all; the transferable-receipt option does.)
- Document that integrators must hold receipts via contracts capable of invoking the claim functions.

### Proof of Concept
Foundry fork PoC sketch (existing harness in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

/// Immutable depositor: can deposit & requestWithdraw through a one-time hook,
/// but has no function to call claimWithdrawRequest.
contract FrozenLender {
    function requestWithdraw(address cdo, uint256 amt, address tranche) external {
        IdleCDOEpochVariant(cdo).requestWithdraw(amt, tranche);
    }
    // deliberately no claim() / no arbitrary call
}

contract FrozenBeneficiaryPoC is IdleCDOEpochQueueTest {
    function testContractBeneficiaryCannotClaim() external {
        FrozenLender lender = new FrozenLender();

        // buffer phase: deposit for the contract, request withdraw of all tranches
        uint256 tranches = _depositWithUser(address(lender), 10000 * ONE_SCALE);
        lender.requestWithdraw(address(cdoEpoch), tranches, address(AAtranche));

        // epoch runs and stops normally; queue processes requests and claims
        _stopCurrentEpochWithApr(10e18);
        vm.prank(manager);
        queue.processWithdrawRequests();
        uint256 epoch = strategy.epochNumber();
        vm.prank(manager);
        queue.processWithdrawalClaims(epoch);

        // entitlement exists and is funded...
        assertGt(queue.userWithdrawalsEpochs(address(lender), epoch), 0);
        assertGt(underlying.balanceOf(address(queue)), 0);

        // ...but the beneficiary has no way to call claimWithdrawRequest(epoch).
        // Entitlement is not transferable, so funds are stuck unless the owner
        // rescues them via transferToken.
        vm.prank(address(lender)); // hypothetical; no code path exists to do this
        // queue.claimWithdrawRequest(epoch); // unreachable for the contract
    }
}
```

The same pattern applies to `IdleCDOEpochVariant.claimWithdrawRequest`/`claimInstantWithdrawRequest` for direct (non-queued) receipts, and to `DefaultDistributor.claim` for post-default proceeds.

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L373-389)
```text
  function claimDepositRequest(uint256 _epoch) external {
    // Deposits can be claimed only after the epoch has been finalized and priced.
    _checkNotAllowed(
      epochPrice[_epoch] == 0 ||
      epochPendingDeposits[_epoch] != 0 ||
      epochPrefundedDeposits[_epoch] != 0
    );

    uint256 amount = userDepositsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_epoch] = 0;
    // transfer tranche tokens to user based on the price of that epoch
    IERC20Detailed(tranche).safeTransfer(msg.sender, amount * ONE_TRANCHE / epochPrice[_epoch]);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L393-411)
```text
  function claimWithdrawRequest(uint256 _epoch) external {
    // check if withdraw requests were processed and claimed for the epoch
    uint256 _withdrawPrice = epochWithdrawPrice[_epoch];
    _checkNotAllowed(
      (_withdrawPrice == 0 && !isEpochWithdrawZero[_epoch]) ||
      epochPendingClaims[_epoch] != 0
    );
    // amount is in tranche tokens
    uint256 amount = userWithdrawalsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user withdraw request counter for the epoch
    userWithdrawalsEpochs[msg.sender][_epoch] = 0;
    // transfer underlyings to user based on the withdraw price of that epoch
    if (_withdrawPrice != 0) {
      IERC20Detailed(underlying).safeTransfer(msg.sender, amount * _withdrawPrice / ONE_TRANCHE);
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L967-979)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L937-951)
```text
  /// @inheritdoc ERC20Upgradeable
  /// @dev Receipt claims are address-bound, so only the IdleCDO can move strategy tokens.
  function _transfer(address sender, address recipient, uint256 amount) internal virtual override {
    if (msg.sender != idleCDO) revert NotAllowed();
    super._transfer(sender, recipient, amount);
  }

  /// @notice Clear the deprecated receipt-token transfer flag.
  /// @dev Kept for upgrade compatibility. Enabling transfers is permanently disabled because
  /// receipt claims are address-bound; the manager may only clear a legacy `true` value.
  /// @param _canTransfer must be false
  function setCanTransfer(bool _canTransfer) external {
    if (msg.sender != manager || _canTransfer) revert NotAllowed();
    canTransfer = false;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L963-965)
```text
  function transferToken(address _token, uint256 value, address _to) external onlyOwner {
    IERC20Detailed(_token).safeTransfer(_to, value);
  }
```

**File:** contracts/DefaultDistributor.sol (L35-41)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
  }
```
