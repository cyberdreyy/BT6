### Title
Missing lower-bound validation on `underlyingsRequested` lets any fulfiller seize escrowed tranche tokens for free - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
CVE-2016-4477 is an input-sanitization bug: `wpa_supplicant` fails to reject dangerous parameter values (`\n`, `\r`) in attacker-controlled `SET`/`SET_CRED`/`SET_NETWORK` commands, letting unprivileged users smuggle unintended directives into a privileged config parser and gain privileges. The closest analog in the credit-vault surface is `IdleCreditVaultWriteOffEscrow`, which likewise stores an attacker/victim-supplied parameter (`underlyingsRequested`) verbatim without any lower-bound or sanity check, and later a privileged-style consumer (`fullfillWriteOffRequest`, callable by any wallet, including an unprivileged write-off fulfiller) enforces only `_underlyings < currentRequest.underlyings → revert`. A request priced at `0` underlyings therefore passes validation, and the fulfiller pays nothing while receiving the full escrowed tranche-token balance. [1](#0-0) [2](#0-1) 

### Finding Description
`createWriteOffRequest` pulls `amount` tranche tokens from the caller and stores `WriteOffRequest{tranches: amount, underlyings: underlyingsRequested}` with only two checks: the epoch must be running and `amount != 0`. There is no check that `underlyingsRequested > 0` or that it is within any economically sane band relative to the tranche value. [1](#0-0) 

`fullfillWriteOffRequest` is permissionless (`@dev this function can be called by any wallet`) and validates the fill only with:

- `currentRequest.tranches == _tranches` (exact tranche amount)
- `_underlyings >= currentRequest.underlyings` (no underpayment)

When `underlyings == 0`, passing `_underlyings = 0` satisfies both constraints. The fulfiller then executes `underlyingToken.safeTransferFrom(msg.sender, this, 0)` (a zero-value `transferFrom` succeeds on USDC/standard ERC20s), the exit fee computes to `_totFee = 0`, and `IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches)` hands the entire escrowed tranche position to the fulfiller. [2](#0-1) 

This mirrors the CVE structurally: a user-controlled parameter is written without rejecting a degenerate value, and a later consumer treats that value as authoritative, producing an outcome (free asset seizure) the protocol never intended.

### Impact Explanation
Direct theft of tranche tokens. A lender who creates a write-off request with `underlyingsRequested = 0` — via UI default, a fat-fingered call, or a front-end that treats the field as optional — permanently loses the full escrowed tranche balance to the first fulfiller (e.g., an MEV bot monitoring `createWriteOffRequest` and `pendingUnderlyings` on-chain). Loss = the full redemption value of the escrowed tranches, received for exactly `0` underlying. The escrow holds real tranche tokens (`safeTransferFrom` on line 93), so this is not accounting-only. [3](#0-2) 

### Likelihood Explanation
Requires a lender to submit `underlyingsRequested = 0`, which is a user error rather than attacker-controlled state, so likelihood is low-to-moderate. However: (a) the field has no protocol-level floor, (b) monitoring bots can atomically fill in the same block, and (c) `fullfillWriteOffRequest` is explicitly permissionless, so no trust assumption protects the victim. The analogous CVE also required only a crafted input accepted by an unsuspecting parser. Uncertainty: whether production front-ends guard this field is outside repo scope; on-chain there is no guard.

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0` (and consider a minimum-price floor vs. current tranche NAV), and in `fullfillWriteOffRequest` require `_underlyings > 0`. Optionally emit the requested price in an event so monitoring can flag degenerate asks before fulfilment.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVaultWriteOffEscrow} from "../contracts/IdleCreditVaultWriteOffEscrow.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract WriteOffZeroPriceTest is Test {
    // Fork mainnet at a block where the escrow is live and isEpochRunning() == true
    IdleCreditVaultWriteOffEscrow escrow =
        IdleCreditVaultWriteOffEscrow(<ESCROW_PROXY>);
    address lender = makeAddr("lender");
    address fulfiller = makeAddr("fulfiller"); // any EOA, no role needed

    function testFreeTrancheSeizure() public {
        IERC20Detailed tranche = IERC20Detailed(escrow.tranche());
        IERC20Detailed underlying = IERC20Detailed(escrow.underlying());

        uint256 trancheAmt = 100e18;
        deal(address(tranche), lender, trancheAmt);

        // Victim creates a request with underlyingsRequested = 0 (accepted, no revert)
        vm.startPrank(lender);
        tranche.approve(address(escrow), trancheAmt);
        escrow.createWriteOffRequest(trancheAmt, 0);
        vm.stopPrank();

        // Unprivileged fulfiller pays 0 underlying, takes all tranches
        vm.prank(fulfiller);
        escrow.fullfillWriteOffRequest(lender, trancheAmt, 0);

        assertEq(tranche.balanceOf(fulfiller), trancheAmt, "fulfiller stole tranches");
        assertEq(underlying.balanceOf(lender), 0, "lender received nothing");
    }
}
```
Zero-amount `safeTransferFrom` succeeds on USDC-class tokens, the fee branch computes `_totFee == 0`, and no guard in the escrow, epoch gating, or KYC whitelist stops the fill.

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
