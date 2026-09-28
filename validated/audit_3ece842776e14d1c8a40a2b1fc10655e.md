### Title
`fullfillWriteOffRequest` DoS when exit fee rounds to zero for revert-on-zero-value tokens - (contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` transfers the exit fee to `feeReceiver` whenever `exitFee > 0`, but does not check that the computed `_totFee` is non-zero. For underlyings amounts where `(_underlyings * exitFee) / FULL_VALUE` rounds to 0, the transfer amount is zero. If the vault's underlying token reverts on zero-value transfers (e.g. tokens with that documented "weird ERC20" behaviour), the call reverts and the write-off request can never be fulfilled — the user's escrowed tranche tokens and expected underlyings stay locked.

### Finding Description
In `fullfillWriteOffRequest`, once the request is validated the escrow pulls underlyings from the fulfiller, then unconditionally executes a fee transfer whenever `exitFee` is set:

```solidity
uint256 _exitFee = exitFee;
uint256 _totFee;
if (_exitFee > 0) {
  _totFee = (_underlyings * _exitFee) / FULL_VALUE;
  // transfer exit fee to the feeReceiver
  underlyingToken.safeTransfer(feeReceiver, _totFee);
}
underlyingToken.safeTransfer(_user, _underlyings - _totFee);
IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
``` [1](#0-0) 

`_totFee` is guarded only by `exitFee > 0`, not by `_totFee != 0`. `createWriteOffRequest`/`deleteWriteOffRequest` correctly gate on zero (`currentRequest.tranches == 0` revert, `IERC20Detailed(tranche).safeTransfer(msg.sender, currentRequest.tranches)` always non-zero), and the IdleCDO layer already uses the defensive pattern `if (_amount == 0) return;` in `_transferUnderlyings`/`_transferUnderlyingsFrom`. The escrow is the one place missing this guard. [2](#0-1) [3](#0-2) 

Reproducing the conditions:
1. Owner sets `exitFee` to a small positive value (e.g. 1 bps-scale) via `setExitFee` (max `MAX_EXIT_FEE`). [4](#0-3) 
2. A tranche holder calls `createWriteOffRequest` for an underlyings amount such that `underlyings * exitFee < FULL_VALUE` — a small write-off request makes `_totFee == 0`. [5](#0-4) 
3. Any fulfiller (borrower or third party) calls `fullfillWriteOffRequest`. `underlyingToken.safeTransfer(feeReceiver, 0)` reverts on a revert-on-zero-transfer token, so fulfillment always fails and the user's tranche tokens remain escrowed.

The same missing guard exists in `deleteWriteOffRequest` only in the trivial `tranches == 0` path already reverted at line 109, so the vulnerable surface is specifically the zero-`_totFee` fee transfer.

### Impact Explanation
A user's write-off request becomes permanently unfulfillable for revert-on-zero tokens: their tranche tokens sit escrowed (they can still self-recover via `deleteWriteOffRequest`, so impact is bounded), and the borrower/third-party fulfiller cannot settle the write-off or obtain the escrowed tranche tokens needed for `writeOffDeposit`. Loss class: temporary freezing of user funds plus blocked write-off settlement; the protocol's write-off flow is bricked for any request whose fee rounds to zero, matching the exact bug class of the external report (zero fee transfer DoS on a user-facing claim/fee path).

### Likelihood Explanation
Likelihood depends on the vault underlying being a revert-on-zero-transfer token and `exitFee > 0` being configured with requests small enough to round the fee to zero. Neither condition requires privileged misbehaviour — an honest owner may set a small fee, and an honest borrower fulfilling small write-off requests is the expected flow. The escrow explicitly accepts requests of any positive size, so small requests are legitimate and common.

### Recommendation
Gate the fee transfer on the computed amount, mirroring `_transferUnderlyings` and the upstream fix:

```solidity
uint256 _totFee;
if (_exitFee > 0) {
  _totFee = (_underlyings * _exitFee) / FULL_VALUE;
  if (_totFee != 0) underlyingToken.safeTransfer(feeReceiver, _totFee);
}
``` [1](#0-0) 

### Proof of Concept
Foundry fork PoC outline:
1. Deploy `IdleCreditVaultWriteOffEscrow` with `underlying` = a mock/mainnet token that reverts on `transfer(x, 0)` and a nonzero `exitFee`.
2. `vm.prank(user)` `createWriteOffRequest(amount)` where `amount * exitFee < FULL_VALUE` (e.g. exitFee=100, FULL_VALUE=1e5, amount=100 → `_totFee=0`).
3. `vm.prank(fulfiller)` `fullfillWriteOffRequest(user, amount, amount)` after approving underlyings → observe revert at `underlyingToken.safeTransfer(feeReceiver, 0)`, request never cleared, `pendingUnderlyings` unchanged.
4. Control: same request on a standard ERC20 succeeds, confirming the DoS is triggered solely by the zero-value transfer.

Uncertainty note: no `safeTransfer(feeReceiver, _totFee)`-style guard is missing elsewhere — IdleCreditVault's recovery/funded-claim paths already return early on zero (`_transferFundedClaim`, `_transferDefaultRecovery` at lines 897-917) and DefaultDistributor's zero-balance claim reverts harmlessly on the caller only.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L91-101)
```text

    // get tranche tokens from user
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L140-151)
```text
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
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L157-161)
```text
  function setExitFee(uint256 _exitFee) external {
    _checkOnlyOwner();
    // check if the exit fee is valid
    if (_exitFee > MAX_EXIT_FEE) revert NotAllowed();
    exitFee = _exitFee;
```

**File:** contracts/IdleCDOCreditVault.sol (L580-582)
```text
  function _transferUnderlyings(address _to, uint256 _amount) internal {
    if (_amount == 0) return;
    IERC20Detailed(token).safeTransfer(_to, _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L587-590)
```text
  function _transferFeeUnderlyings(uint256 _amount) internal {
    uint256 feeReceiverAmount = _feeReceiverAmount(_amount);
    _transferUnderlyings(feeReceiver, feeReceiverAmount);
    _transferUnderlyings(owner(), _amount - feeReceiverAmount);
```
