### Title
Zero-price write-off request lets any fulfiller seize escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
The Froxlor report describes a business-logic error where mandatory fields (username, surname, company) could be left blank to bypass required-field validation. The analog in `IdleCreditVaultWriteOffEscrow` is `createWriteOffRequest`: it enforces `amount != 0` (the tranche deposit is mandatory) but never validates `underlyingsRequested`, the price field of the request. A request created with `underlyingsRequested = 0` is then fulfillable by anyone paying 0 underlying, because `fullfillWriteOffRequest` only checks `_underlyings >= currentRequest.underlyings` — which is trivially satisfied by 0. [1](#0-0) [2](#0-1) 

### Finding Description
`createWriteOffRequest(uint256 amount, uint256 underlyingsRequested)` deposits `amount` tranche tokens into escrow and stores `WriteOffRequest{tranches, underlyings}`. It reverts on `amount == 0` but accepts `underlyingsRequested == 0` without complaint — the "mandatory" price field can be left blank.

`fullfillWriteOffRequest(_user, _tranches, _underlyings)` validates the request with:

```solidity
if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
  revert WrongRequest();
}
```

When `currentRequest.underlyings == 0`, the second clause can never fail, so a fulfiller passes `_underlyings = 0`. The function then executes `underlyingToken.safeTransferFrom(msg.sender, address(this), 0)` (a no-op), sends the user `0 - _totFee = 0` underlying, and transfers the full escrowed `tranches` balance to the fulfiller. The tranche tokens represent a real claim on vault NAV; the fulfiller can hold them, redeem them at epoch end, or (if the borrower) burn them via `writeOffDeposit` to erase debt — all while paying nothing.

The invariant broken is the escrow's exchange guarantee: tranche tokens escrowed for debt sale must only be released for at least the requested consideration. With the price field blank, the release condition degenerates to "pay anything ≥ 0" i.e. nothing.

### Impact Explanation
Any lender who creates a request without specifying a price — e.g., a UI that defaults the field to 0, a partially-filled form, or a second `createWriteOffRequest` call that passes `underlyingsRequested = 0` to top up tranches — has their entire escrowed tranche position exposed. The first EOA to call `fullfillWriteOffRequest` takes all escrowed tranche tokens for zero cost. Loss equals the full value of escrowed tranche tokens; the lender receives nothing (0 underlying minus a 0 fee). This is direct theft of user funds by an unprivileged actor.

### Likelihood Explanation
The attack path requires a victim to have left `underlyingsRequested == 0`. The contract gives no protection or warning: unlike `amount`, the price field has no `Is0`/`NotAllowed` guard, and there is no way for a user to distinguish "request pending" from "request has no price". Because fulfilment is permissionless (`fullfillWriteOffRequest` "can be called by any wallet"), any keeper or MEV-style watcher can drain such requests atomically the moment they exist — even in the same block via `createWriteOffRequest` front-run observation. No privileged role, timing window, or oracle dependence is required once the malformed request exists.

### Recommendation
In `createWriteOffRequest`, revert when `underlyingsRequested == 0` (e.g. `if (underlyingsRequested == 0) revert Is0();`), matching the existing non-zero enforcement on `amount`. Optionally also require `currentRequest.underlyings > 0` inside `fullfillWriteOffRequest` so that legacy zero-price requests cannot be fulfilled for free — such users can recover their tranches via `deleteWriteOffRequest` and recreate a priced request.

### Proof of Concept
Foundry fork test sketch (setup helpers follow `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
function testFreeFulfilmentOfZeroPriceRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // lender deposits, epoch running
    uint256 trancheBal = AAtranche.balanceOf(address(this));
    AAtranche.approve(address(escrow), trancheBal);

    // lender creates request but leaves price blank (0 underlyings)
    escrow.createWriteOffRequest(trancheBal, 0);

    address fulfiller = address(0xbeef);
    // fulfiller pays nothing, receives all escrowed tranche tokens
    vm.prank(fulfiller);
    escrow.fullfillWriteOffRequest(address(this), trancheBal, 0);

    assertEq(AAtranche.balanceOf(fulfiller), trancheBal, "fulfiller got tranches for free");
    assertEq(underlying.balanceOf(address(this)), 0, "lender received nothing");
}
```

All checks pass: `isEpochRunning()` is true during a running epoch, `currentRequest.tranches == _tranches`, and `_underlyings (0) >= currentRequest.underlyings (0)`, so no guard intervenes.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-102)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

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
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-155)
```text
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[_user];

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
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);

    // If the fulfiller is the borrower, they can then choose to either keep the tranche tokens or write them off via
    // IdleCDOEpochVariant.writeOffDeposit method.
  }
```
