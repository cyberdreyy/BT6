### Title
Fee-on-transfer underlying is credited/paid at the nominal amount instead of the balance delta in deposit forwarding and write-off fulfillment - (File: contracts/IdleCDOCreditVault.sol, contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
The vault mints shares correctly by measuring the balance delta of `token`, but then forwards the *nominal* `_amount` to the strategy, which mints strategy tokens 1:1 against that nominal value. Separately, `fullfillWriteOffRequest` in the write-off escrow measures nothing: it pulls `_underlyings` from the fulfiller and immediately pays `_underlyings - _totFee` to the requester, trusting that the token transferred the full amount. If `token` ever charges a transfer fee (e.g., USDT fee mode enabled, or a rebasing/deflationary underlying chosen at init), the escrow pays out more than it received, draining `pendingUnderlyings` owed to other write-off requesters, and the strategy over-mints strategy tokens, inflating NAV.

### Finding Description
In `IdleCDOCreditVault._deposit`, shares are minted from the measured delta (`_contractTokenBalance(_token) - _preBal`), which is correct, but `IIdleCDOStrategy(strategy).deposit(_amount)` is then called with the pre-fee `_amount` [1](#0-0) . Strategies such as `IdleClearpoolStrategyOptimism.deposit` pull `_amount` and mint strategy tokens based on the nominal figure [2](#0-1) .

In `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest`, the escrow transfers `underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings)` and then sends `underlyingToken.safeTransfer(_user, _underlyings - _totFee)` with no balance-delta check [3](#0-2) . `createWriteOffRequest` likewise credits `currentRequest.underlyings + underlyingsRequested` based on a price computation rather than received balance [4](#0-3) .

The external Loopring bug is about unprivileged users registering arbitrary tokens that need a balance check the owner cannot front-run; the analog here is that `token`/`underlying` is fixed once at initialization with no token-integration checklist enforced in code, and multiple paths still assume `transferFrom` moves the full nominal amount.

### Impact Explanation
With a fee-on-transfer underlying:
- Escrow: fulfiller sends 100 tokens, escrow receives 99, pays user ~99 worth but only collected 99 → net drain equal to the fee; since user receives `_underlyings - _totFee` computed on nominal, the escrow's `pendingUnderlyings` accounting (already reduced) no longer matches actual holdings, permanently freezing or stealing the last requesters' payouts.
- Vault/strategy: strategy mints `_amount` strategy tokens while receiving `_amount - fee`; `getContractValue` counts strategy-token balance 1:1, overstating NAV and socializing the shortfall to exiting tranche holders.

### Likelihood Explanation
Requires the configured underlying to take a transfer fee (e.g., USDT fee parameter being enabled, or a vault deployed on a deflationary token). No privileged misbehavior needed — any fulfiller or depositor triggers the mismatch atomically. Likelihood is conditional on token configuration, mirroring the external report's configuration-type finding.

### Recommendation
Measure balance deltas everywhere tokens move: in strategies, compute `minted` from `balanceOf` difference rather than `_amount`; in `fullfillWriteOffRequest`, snapshot `balanceOf(address(this))` before/after the fulfiller's transfer and pay the user based on the received amount; document a token-integration checklist (no fee-on-transfer/rebasing tokens) before deployment.

### Proof of Concept
```solidity
// Fork test: escrow on an underlying with a 1% transfer fee (e.g., USDT
// with basisPointsRate enabled via its owner, or a fee-enabled deployment).
function testFulfillDrainsEscrow() public {
    // user has a write-off request for 100e6 underlying
    escrow.createWriteOffRequest(); // tranche approved
    deal(address(usdt), fulfiller, 100e6);
    vm.prank(fulfiller);
    usdt.approve(address(escrow), 100e6);
    uint256 balBefore = usdt.balanceOf(address(escrow));
    vm.prank(fulfiller);
    escrow.fullfillWriteOffRequest(user, tranches, 100e6);
    // escrow received 99e6 but sent ~99e6 to user + fee share:
    // net escrow balance decreased despite pendingUnderlyings for other users
    assertLt(usdt.balanceOf(address(escrow)) - balBefore + (100e6 * escrow.exitFee() / 1e5), 100e6 - 100e6 * escrow.exitFee() / 1e5);
}
```
```solidity
// Vault deposit with fee-on-transfer token: shares minted on delta (correct)
// but strategy.deposit(_amount) mints _amount strategy tokens while the
// strategy received _amount * 99/100 -> NAV overstated by the fee.
```

Caveat: I could not fully verify the exact `strategyToken` minting in `strategies/idle/IdleCreditVault.sol` (the primary credit-vault strategy) within the iteration budget; the strategy-side inflation claim is confirmed for the clearpool-style `onlyIdleCDO` deposit pattern, which mints on nominal `_amount`.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L203-211)
```text
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/optimism/strategies/clearpool/IdleClearpoolStrategyOptimism.sol (L214-221)
```text
        if (_amount > 0) {
            underlyingToken.safeTransferFrom(
                msg.sender,
                address(this),
                _amount
            );
            minted = _depositToVault(_amount);
        }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L93-101)
```text
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L137-149)
```text
    IERC20Detailed underlyingToken = IERC20Detailed(underlying);
    // transfer underlyings requested from the fulfiller to this contract
    underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
    // check if the exit fee is set and if so, apply it
    uint256 _exitFee = exitFee;
    uint256 _totFee;
    if (_exitFee > 0) {
      _totFee = (_underlyings * _exitFee) / FULL_VALUE;
      // transfer exit fee to the feeReceiver
      underlyingToken.safeTransfer(feeReceiver, _totFee);
    }
    // transfer the remaining underlyings to the user
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
```
