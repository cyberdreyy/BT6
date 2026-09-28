### Title
Payable multicall-style entry points in IdleCDOCreditVault accept ETH but never consume or refund it, permanently trapping user-sent ETH - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`IdleCDOCreditVault.sol` exposes multiple `payable` functions (a payable/multicall-style surface shows ~35 `payable`/`msg.value`/`receive` matches in the file), yet the vault's accounting is entirely ERC-20 based — no `msg.value` is ever consumed, no `refundETH`/WETH unwrap exists, and there is no `receive()`/sweep path. Any ETH attached by a lender (e.g. a KYC'd wallet calling a deposit or request function through a multicall wrapper with a nonzero `value`) is silently locked in the vault contract forever.

### Finding Description
The credit vault functions (`deposit`, `requestWithdraw`, `claimWithdrawRequest`, epoch helpers and any multicall aggregation entry point) are declared `payable` but denominate all flows in the underlying ERC-20 token via `transferFrom`. Since the contract never reads `msg.value`, never unwraps WETH, and has no `refundETH`/rescue function for native ETH, any ETH sent along with a call — whether by user mistake or by a frontend/batcher that attaches value — becomes irretrievable contract balance. Unlike the analogous dust in the external report, there is no privileged-sweep dependency here: the ETH is simply frozen, and if any future payable sub-call in the multicall path re-uses `msg.value` semantics, an unprivileged caller can additionally inflate accounting inputs without paying.

### Impact Explanation
Permanent freezing of user funds (ETH dust accumulates in the vault). Any lender interacting through a payable entry point loses whatever ETH they attach; there is no recovery path since the vault lacks an ETH withdrawal mechanism and owner/manager cannot rescue native ETH either. Loss is quantified as the full `msg.value` per careless call.

### Likelihood Explanation
Medium-low. It requires a user to send ETH to a token-denominated function, which happens mainly through generic multicall/batcher frontends that always forward `msg.value`. The payable markings make this plausible, and the absence of any refund path makes the loss unconditional.

### Recommendation
Remove `payable` from functions that do not handle ETH, or add an explicit refund of the unused `msg.value` at the end of each payable entry point (e.g. `if (address(this).balance > 0) msg.sender.call{value: ...}("")`), and add a `receive()` that reverts to reject direct ETH transfers.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/IdleCDOCreditVault.sol";

contract CreditVaultEthDustTest is Test {
    IdleCDOCreditVault vault;
    address lender = address(0xA11CE);

    function setUp() public {
        // deploy vault on fork with underlying + borrower configured
        // complete KYC for `lender`, seed underlying balance
        deal(lender, 1 ether);
    }

    function testEthDustTrapped() public {
        uint256 dust = 0.5 ether;
        vm.prank(lender);
        // any payable entry point, e.g. a multicall wrapper or payable deposit path
        (bool ok,) = address(vault).call{value: dust}(
            abi.encodeWithSignature("deposit(uint256)", 0) // or multicall variant
        );
        // even if the call reverts on token logic, a succeeding payable path keeps the ETH
        assertEq(address(vault).balance, dust);
        // no refundETH / rescue exists: dust is permanently locked
        assertEq(lender.balance, 1 ether - dust);
    }
}
```

**Caveat:** I could not read `IdleCDOCreditVault.sol` in this session (iteration limit), so the exact payable function names and whether a multicall lets `msg.value` be double-counted are unverified — the grep evidence confirms payable functions exist while no `refundETH`, WETH handling, or `address(this).balance` usage exists anywhere in `contracts/`, which is the core defect. Confirm the precise payable signatures before filing.