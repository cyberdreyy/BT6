### Title
Owner/guardian cannot call admin functions (pause/emergencyShutdown/restoreOperations) during Arbitrum sequencer downtime due to L1→L2 address aliasing - (File: contracts/IdleCDO.sol)

### Summary
All privileged entry points in `IdleCDO`/`IdleCDOCreditVault`/`IdleCDOEpochVariant` gate on `msg.sender == owner()` or `msg.sender == guardian` (`_checkOnlyOwner`, `_checkOnlyOwnerOrGuardian`). The Idle tranche vaults are deployed on Arbitrum and Optimism (see `.openzeppelin/arbitrum-one.json`, `.openzeppelin/optimism.json`, and the Optimism fork test for `HypernativeBatchPauser`). On Arbitrum, when the sequencer is down, messages forced through the delayed inbox from L1 arrive with `msg.sender` aliased (`L1_address + 0x1111...1111`), so an L1 owner/guardian multisig or EOA cannot satisfy the `msg.sender`-based authorization checks. The admin is blocked from pausing, unpausing, running `emergencyShutdown`, `restoreOperations`, `updateAccounting`, or `transferToken` precisely when emergency intervention may be most needed.

### Finding Description [1](#0-0) [2](#0-1) 

`_checkOnlyOwner()` compares `owner() != msg.sender` and `_checkOnlyOwnerOrGuardian()` compares `msg.sender != guardian && msg.sender != owner()`. None of these checks account for the Arbitrum L2 alias of the owner. When the sequencer is down, the only path to execute transactions is a forced L1→L2 message, and `msg.sender` on L2 becomes `AddressAliasHelper.applyL1ToL2Alias(l1Sender)`, which differs from `owner()`/`guardian`, so every admin call reverts with `NotAuthorized()`.

Affected functions include `emergencyShutdown()`, `pause()`, `unpause()`, `restoreOperations()`, `updateAccounting()`, `transferToken()` (the recovery-fund sweep in `GuardedLaunchUpgradable.sol:70-73`), and all owner setters. There is no alternate access path: `HypernativeBatchPauser.pauseAll()` is itself gated on `msg.sender == pauser || msg.sender == owner()`, so it is equally bricked under aliasing.

### Impact Explanation
Temporary freezing of user funds and loss of emergency response capability:

- During a sequencer outage that coincides with an exploit, oracle malfunction, or borrower default, the guardian/owner cannot call `emergencyShutdown()`/`pause()` to halt deposits and withdrawals, so an unprivileged attacker can keep interacting with a compromised vault while the admin is locked out.
- If the vault was already paused before the outage, `unpause()`/`restoreOperations()` cannot be invoked for the duration of the outage (forced transactions revert on the auth check), extending the freeze of all depositor funds. In `IdleCDOEpochVariant`, `restoreOperations()` is also the only path to re-enable withdrawal requests after an emergency.
- `transferToken()` cannot be used to sweep funds to `governanceRecoveryFund`, and `updateAccounting()` cannot be called to crystallize a BB-first loss.

Loss duration equals the sequencer downtime plus remediation; funds remain withdrawable/exploitable or frozen with no admin recourse.

### Likelihood Explanation
Requires the conjunction of an Arbitrum (or other alias-applying L2) sequencer outage and a need for admin action during that window. Sequencer outages have occurred multiple times historically on Arbitrum and Optimism; the aliasing behavior is deterministic and unconditional, so whenever it happens the lockout is guaranteed for any owner/guardian operating from L1 or via a Safe whose L2 `msg.sender` path is forced through the delayed inbox. No unprivileged attacker action is required to trigger it, but an attacker could deliberately time an exploit for a known outage.

### Recommendation
Allow the L2 alias of privileged addresses to satisfy admin checks when the transaction originates via the Arbitrum delayed inbox, e.g. extend `_checkOnlyOwnerOrGuardian`/`_checkOnlyOwner` to also accept `AddressAliasHelper.undoL1ToL2Alias(msg.sender) == owner()` (or guardian). Alternatively, register a dedicated L2-native backup guardian/pauser so that at least one privileged actor retains a non-aliased `msg.sender` path during sequencer downtime.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IIdleCDO} from "../../contracts/interfaces/IIdleCDO.sol";

// Simulates an Arbitrum forced-inclusion call: msg.sender is the L1->L2 alias
// of the real owner while the sequencer is down.
contract SequencerDownAdminLockout is Test {
  address constant CDO     = 0x94e399Af25b676e7783fDcd62854221e67566b7f; // IdleCDO on Arbitrum
  address constant L1_OWNER = 0xFDbB4d606C199F091143BD604C85c191a526fbd0; // TL multisig / owner

  address constant ALIAS_OFFSET = 0x1111000000000000000000000000000000001111;

  function testAdminLockedOutDuringSequencerDown() public {
    vm.createSelectFork("arbitrum");

    address aliasedOwner = address(uint160(L1_OWNER) + uint160(ALIAS_OFFSET));

    // Owner forces a transaction from L1 while sequencer is down.
    // On Arbitrum, L2 sees the aliased address as msg.sender.
    vm.prank(aliasedOwner);
    vm.expectRevert(); // NotAuthorized()
    IIdleCDO(CDO).emergencyShutdown();

    vm.prank(aliasedOwner);
    vm.expectRevert(); // NotAuthorized()
    IIdleCDO(CDO).pause();

    // Sanity: the un-aliased owner (normal sequencer path) succeeds.
    vm.prank(L1_OWNER);
    IIdleCDO(CDO).pause();
    assertTrue(IIdleCDO(CDO).paused());
  }
}
```

### Citations

**File:** contracts/GuardedLaunchUpgradable.sol (L53-55)
```text
  function _checkOnlyOwner() internal view {
    _checkNotAuthorized(owner() != msg.sender);
  }
```

**File:** contracts/IdleCDO.sol (L985-987)
```text
  function _checkOnlyOwnerOrGuardian() internal view {
    _checkNotAuthorized(msg.sender != guardian && msg.sender != owner());
  }
```
