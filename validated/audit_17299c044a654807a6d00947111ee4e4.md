### Title
KYC revocation / whitelist removal can be front-run or evaded by transferring unrestricted tranche tokens or pre-staging withdraw requests - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` gates deposits and withdraw *requests* behind `isWalletAllowed` (Keyring credential or `KeyringIdleWhitelist` status), but tranche tokens are plain unrestricted ERC20s and all claim paths pay out without re-checking KYC. When the admin sends `setWhitelistStatus(Alice, false)` (or the Keyring credential for Alice is revoked), Alice can see the pending transaction and front-run it — either by transferring her tranche tokens to a second KYC-passing address she controls, or by submitting `requestWithdraw` before revocation lands and claiming after. The denylist action therefore fails to contain the targeted wallet, exactly the bug class of `setConvictionless` front-running in the external report.

### Finding Description
The KYC gate is enforced only at *entry points*:

- `_deposit` and `depositDuringEpoch` revert unless `isWalletAllowed(msg.sender)` [1](#0-0) 
- `requestWithdraw` in `IdleCDOEpochQueue` calls `_checkAllowed(msg.sender)` [2](#0-1) 
- `isWalletAllowed` resolves to `IKeyring(keyring).checkCredential(...)` or the admin whitelist [3](#0-2) 
- The admin denylist mechanism is `KeyringIdleWhitelist.setWhitelistStatus(entity, false)`, a public mempool transaction [4](#0-3) 

But the exits are unrestricted:

- `IdleCDOTranche` is a vanilla `ERC20` with no `_beforeTokenTransfer` hook — tranche tokens move freely to any address [5](#0-4) 
- `claimWithdrawRequest` / `claimInstantWithdrawRequest` on the CDO forward to the vault with no `isWalletAllowed` check [6](#0-5) 
- Queue claims (`claimDepositRequest`, `claimWithdrawRequest`) pay `msg.sender` with no credential re-check — accounting is keyed to the requesting address only [7](#0-6) 

Attack sequence (running epoch, epoch-based credit vault):

1. Alice is a KYC'd lender holding AA tranche tokens. Protocol admin observes misbehavior/sanction and submits `setWhitelistStatus(Alice, false)` (or initiates Keyring credential revocation).
2. Alice sees the mempool tx and front-runs it with `tranche.transfer(Alice2, bal)` where Alice2 is her own KYC-passing wallet — trivially satisfiable since the whitelist check is per-address and Keyring policies pass EOAs they onboard. Alternatively she calls `requestWithdraw` herself before the revocation confirms.
3. The denylist tx lands; `whitelist[Alice] == false`. Alice2 calls `requestWithdraw` (passes `isWalletAllowed`) and after `stopEpoch` calls `claimWithdrawRequest`, which pays out with no KYC re-check. Or, in the pre-staged variant, Alice simply calls `claimWithdrawRequest` post-revocation — it never checks `isWalletAllowed`.

A non-KYC'd holder can also acquire tranche tokens secondarily (the WriteOffEscrow fulfill path explicitly allows non-Keyring buyers, confirmed in `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw`) and exit via a KYC'd intermediary, since the token itself carries no transfer restriction.

### Impact Explanation
The KYC/denylist control fails to achieve its purpose: a lender whom the admin intends to cut off — e.g., a sanctioned or misbehaving address — can always complete a full deposit→request→claim cycle and withdraw pool funds after revocation is initiated. In epoch modes where withdrawal timing matters (buffer period claims at `epochPrice`/`epochWithdrawPrice`), the revoked user exits with the same pro-rata payout as honest lenders, defeating the containment that motivated the denylist entry. If the revocation was meant to freeze the position pending investigation or to block a compromised address, that freezing is permanently lost — the funds leave the vault to the attacker-controlled second wallet.

### Likelihood Explanation
Likelihood is moderate-to-high whenever denylisting is actually exercised: revocation txs are visible in the public mempool, evasion requires only a second KYC'd address (or a pre-staged request), and no guard re-checks credentials at claim time. The only mitigating factor is whether operators treat KYC revocation as a compliance-only signal rather than a security freeze — but the whitelist mechanism (`setWhitelistStatus`) exists precisely to revoke access, and its bypass requires no privileged collusion, only unprivileged transactions.

### Recommendation
- Check `isWalletAllowed` at claim time (`claimWithdrawRequest`, `claimInstantWithdrawRequest`, and queue `claimDepositRequest`/`claimWithdrawRequest`), or bind claims to the credential state at request time.
- Alternatively, add a commit/reveal or timelock to `setWhitelistStatus` removals (mirroring the external report's recommendation) so the target cannot front-run the denylist entry — noting this does not fix the claim-side gap.
- If tranche tokens are intended to be KYC-bound, add a `_beforeTokenTransfer` hook on `IdleCDOTranche` enforcing `isWalletAllowed(to)`; otherwise document that KYC is per-transaction, not per-position.

### Proof of Concept
Foundry fork-style scenario (against the suite's existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Alice = KYC'd LP; admin submits setWhitelistStatus(Alice, false)
// Alice front-runs:
vm.prank(alice);
AAtranche.transfer(alice2, aliceBal); // unrestricted ERC20

// admin tx lands
vm.prank(admin);
KeyringIdleWhitelist(kw).setWhitelistStatus(alice, false);
assertFalse(cdoEpoch.isWalletAllowed(alice));

// alice2 (KYC'd) requests and claims; or alice claims a pre-staged request
vm.prank(alice2);
cdoEpoch.requestWithdraw(aliceBal, address(AAtranche)); // passes: alice2 allowed
_stopCurrentEpoch();
vm.prank(alice2);
cdoEpoch.claimWithdrawRequest(); // no isWalletAllowed check -> pays out
```

The same holds by submitting `requestWithdraw` from `alice` before the revocation tx and calling `claimWithdrawRequest` after, since no claim path re-checks `isWalletAllowed`.

One caveat I could not fully verify within the iteration limit: whether `IdleCreditVault.claimWithdrawRequest`/`claimInstantWithdrawRequest` apply their own credential check internally (the CDO-side wrapper does not). Even if the vault re-checks, the front-run via token transfer to a KYC'd second address remains fully effective, so the finding stands on the transferability alone.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L643-669)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }

  /// @notice Deposit during an active epoch with prorated interest
  /// @param _amount Amount of underlyings
  /// @param _tranche Tranche to deposit into
  /// @return _minted Amount of tranche tokens minted
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
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

**File:** contracts/IdleCDOEpochVariant.sol (L984-987)
```text
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L131-142)
```text
  function requestWithdraw(uint256 amount) external nonReentrant {
    // check if the wallet is allowed to deposit (ie epoch is running and keyring KYC completed)
    _checkAllowed(msg.sender);
    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // withdraw requests will be made in the next buffer period (ie next epoch)
    uint256 nextEpoch = IdleCreditVault(strategy).epochNumber() + 1;
    // updated user queued withdraw amount for the next epoch. Epoch number
    // is considered the epoch when processWithdrawRequests will be called
    userWithdrawalsEpochs[msg.sender][nextEpoch] += amount;
    // update pending withdraw requests
    epochPendingWithdrawals[nextEpoch] += amount;
```

**File:** contracts/IdleCDOEpochQueue.sol (L373-410)
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

  /// @notice claim withdraw request
  /// @param _epoch epoch when withdraw request were processed
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
```

**File:** contracts/KeyringIdleWhitelist.sol (L102-111)
```text
  function setWhitelistStatus(address entity, bool status) external {
    if (msg.sender != admin) {
      revert NotAdmin(msg.sender);
    }
    bool oldStatus = whitelist[entity];
    if (oldStatus == status) {
      return; // No change in status, so no event emission.
    }
    whitelist[entity] = status;
    emit Whitelist(entity, status);
```

**File:** contracts/IdleCDOTranche.sol (L7-41)
```text
contract IdleCDOTranche is ERC20 {
  // allowed minter address
  address public minter;
  // liquidity burned at first tranche deposit
  uint256 internal constant MIN_LIQUIDITY = 10**3;

  /// @param _name tranche name
  /// @param _symbol tranche symbol
  constructor(
    string memory _name, // eg. IdleDAI
    string memory _symbol // eg. IDLEDAI
  ) ERC20(_name, _symbol) {
    // minter is msg.sender which is IdleCDO (in initialize)
    minter = msg.sender;
  }

  /// @param account that should receive the tranche tokens
  /// @param amount of tranche tokens to mint
  function mint(address account, uint256 amount) external {
    require(msg.sender == minter, '6');
    // burn MIN_LIQUIDITY on first tranche deposit
    if (totalSupply() == 0) {
      _mint(address(1), MIN_LIQUIDITY);
      amount -= MIN_LIQUIDITY;
    }
    _mint(account, amount);
  }

  /// @param account that should have the tranche tokens burned
  /// @param amount of tranche tokens to burn
  function burn(address account, uint256 amount) external {
    require(msg.sender == minter, '6');
    _burn(account, amount);
  }
}
```
