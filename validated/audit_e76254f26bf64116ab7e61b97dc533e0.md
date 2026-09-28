### Title
Write-off escrow accepts requests with zero `underlyingsRequested`, allowing fulfillers to seize escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
Analogous to the appchain-registry report (a registration function that does not enforce required fields), `IdleCreditVaultWriteOffEscrow.createWriteOffRequest` accepts a `underlyingsRequested` value of `0`. Because `fullfillWriteOffRequest` only enforces `_underlyings >= currentRequest.underlyings`, a request priced at 0 underlyings can be fulfilled by transferring nothing, while the fulfiller still receives all escrowed tranche tokens. The missing required-field validation converts a data-entry gap into permissionless theft of escrowed tranche tokens.

### Finding Description
In `createWriteOffRequest`, only `amount == 0` is rejected; `underlyingsRequested` is never validated:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol:86-101
function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
  if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
  if (amount == 0) revert NotAllowed();
  IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
  ...
  userRequests[msg.sender] = WriteOffRequest({
    tranches: currentRequest.tranches + amount,
    underlyings: currentRequest.underlyings + underlyingsRequested
  });
  pendingUnderlyings += underlyingsRequested;
}
```

`fullfillWriteOffRequest` then only requires `_underlyings >= currentRequest.underlyings` (`contracts/IdleCreditVaultWriteOffEscrow.sol:129`), which is trivially satisfied by `_underlyings = 0` when the request was priced at 0, and transfers `_tranches` to `msg.sender` (`line 151`). There is also no upper-bound sanity check: an attacker can register `underlyingsRequested = type(uint256).max` with dust tranches to permanently inflate `pendingUnderlyings`, since no honest fulfiller can ever satisfy it (a freezing/DoS variant).

### Impact Explanation
Direct theft of escrowed tranche tokens. Any tranche tokens escrowed against a request whose aggregate `underlyings` is 0 (or that an attacker can force to 0 via additive requests) can be pulled out by any unprivileged fulfiller paying nothing — the check `_underlyings < currentRequest.underlyings` passes at 0 and the exit-fee branch is skipped entirely (`_totFee = 0`). The escrow's core invariant — tranches only leave in exchange for at least the requested underlyings — is broken by the missing required-field validation. Note the honest caveat: exploitation requires a request whose `underlyings` is 0, which the lender controls; the code's failure is not enforcing the field, so a mispriced request (including partial updates that push underlyings toward zero relative to tranches) is irreversibly capturable by any fulfiller rather than being rejected at creation.

### Likelihood Explanation
Fulfillment is permissionless ("can be called by any wallet", line 122 comment), so any EOA can monitor `userRequests`/`pendingUnderlyings` and fulfill a zero-priced request atomically — no privileged role needed and no epoch gating beyond `isEpochRunning()` at creation. Likelihood is bounded by the need for a zero-priced (or effectively zero-priced) request to exist; a lender-facing UI not surfacing the field or a second `createWriteOffRequest` call that adds tranches without adding underlyings (the additive update at lines 97-100 silently allows `tranches` to grow while `underlyings` stays 0) both create this state on-chain with no revert.

### Recommendation
Enforce required fields at creation and fulfillment, mirroring the Octopus fix pattern:
- In `createWriteOffRequest`: `if (underlyingsRequested == 0) revert NotAllowed();` so `tranches` can never be escrowed against a zero price.
- In `fullfillWriteOffRequest`: additionally require `_underlyings > 0` (and consider requiring `currentRequest.underlyings > 0`) before settling.
- Consider capping `underlyingsRequested` relative to the tranche's NAV to prevent `pendingUnderlyings` griefing with unfulfillable max-uint requests.

### Proof of Concept
Foundry fork PoC (epoch running, AA escrow `escrow`, underlying `underlying`):

```solidity
function testFulfillZeroPricedRequest() external {
  // lender (or a second additive call) leaves a request with 0 underlyings
  deal(tranche, lender, 100e18);
  vm.startPrank(lender);
  IERC20(tranche).approve(address(escrow), 100e18);
  // missing validation: underlyingsRequested = 0 is accepted
  escrow.createWriteOffRequest(100e18, 0);
  vm.stopPrank();

  // unprivileged fulfiller pays nothing and takes all escrowed tranches
  address fulfiller = makeAddr("fulfiller");
  vm.prank(fulfiller);
  escrow.fullfillWriteOffRequest(lender, 100e18, 0);

  assertEq(IERC20(tranche).balanceOf(fulfiller), 100e18);
  assertEq(IERC20(underlying).balanceOf(lender), 0); // lender received nothing
}
```