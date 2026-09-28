### Title
`HypernativeBatchPauser.pauseAll` permanently reverts unless the `pauser` also holds the guardian/owner role on every protected CDO — ([File: contracts/HypernativeBatchPauser.sol](contracts/HypernativeBatchPauser.sol))

### Summary
`pauseAll()` is the designated single-transaction emergency brake: a dedicated `pauser` address (or the owner) can pause every protected IdleCDO at once. However, `pauseAll` performs a raw external call to `IIdleCDO.emergencyShutdown()` in a loop with no `try/catch`, and `emergencyShutdown()` independently reverts for any caller that is not that CDO's `guardian` or `owner` (`_checkOnlyOwnerOrGuardian`). One protected contract on which `pauser` is not also the guardian makes the entire batch call revert — the exact same bug class as the Cooler report, where an "emergency_shutdown" role could not run `emergencyShutdown` because the internal `defund` call required the separate "cooler_overseer" role.

### Finding Description
- `HypernativeBatchPauser.pauseAll` authorizes only `msg.sender == pauser || msg.sender == owner()` and then calls `emergencyShutdown()` on every entry of `protectedContracts` in a bare loop [1](#0-0) .
- `IdleCDO.emergencyShutdown()` (and the credit-vault equivalent) reverts via `_checkOnlyOwnerOrGuardian` unless `msg.sender == guardian || msg.sender == owner()` [2](#0-1) [3](#0-2) [4](#0-3) .
- Since `msg.sender` inside each `emergencyShutdown` is the `HypernativeBatchPauser` contract (not the EOA `pauser`), each protected CDO would actually need the *pauser contract itself* to be its guardian. Guardianship is per-CDO (`guardian = _owner` at init, changeable via `setGuardian`) [5](#0-4) , and `protectedContracts` is set freely by the batch-pauser owner with no consistency check against each CDO's guardian [6](#0-5) .
- There is no `try/catch` or continue-on-failure, so a single misconfigured entry reverts the whole transaction atomically — none of the contracts get paused, including the ones the pauser *is* guardian of.

### Impact Explanation
In an active exploit or strategy failure, the designated emergency responder (`pauser`) cannot pause the fleet. Because `emergencyShutdown` on an unauthorized CDO reverts, the whole `pauseAll` reverts: zero contracts are paused, deposits and withdrawals stay open, and an unprivileged attacker can keep depositing/withdrawing (e.g., continue extracting value during the exploit window that the batch pauser was deployed to prevent). This is a permanent failure of the emergency path for any deployment where at least one protected CDO's guardian does not equal the pauser contract — a plausible steady state since guardians are per-vault and `protectedContracts` is managed independently. Loss = funds drained during the unblocked window; worst case is full NAV minus what individual owner multisigs can later rescue.

### Likelihood Explanation
High configuration sensitivity, medium trigger likelihood. It requires only that `protectedContracts` contain a CDO whose guardian differs from the `HypernativeBatchPauser` contract address — no attacker precondition. Once configured this way, `pauseAll` deterministically reverts every time it is needed. Per the audit rules the privileged roles (owner, guardian, manager) are honest; the bug is in the cross-contract role plumbing, not a malicious actor, matching the Cooler finding's "role A cannot perform its designated emergency action because a dependency requires role B" pattern.

### Recommendation
- Wrap each `emergencyShutdown` call in `try/catch` so one unauthorized/failed contract cannot block pausing the rest, and emit an event listing failures for off-chain follow-up.
- Alternatively (or additionally), standardize the guardian role: have `addProtectedContracts`/`replaceProtectedContracts` verify `IIdleCDO(cdo).guardian() == address(this)` at registration, or set each protected CDO's guardian to the pauser contract so the single emergency role genuinely controls the batch pause.

### Proof of Concept
Foundry fork/unit test sketch:

```solidity
// test/foundry/HypernativeBatchPauser.t.sol
function testPauseAllRevertsWhenPauserNotGuardian() public {
    // cdo1 guardian = pauser contract (OK), cdo2 guardian = some other address
    address[] memory cds = new address[](2);
    cds[0] = address(idleCDO1); // guardian == address(batchPauser)
    cds[1] = address(idleCDO2); // guardian == owner != batchPauser

    HypernativeBatchPauser pauser = new HypernativeBatchPauser(pauserEOA, cds);

    vm.prank(pauserEOA);
    vm.expectRevert(GuardedLaunchUpgradable.NotAuthorized.selector);
    pauser.pauseAll(); // reverts on cdo2; cdo1 also remains unpaused (atomic revert)

    assertFalse(idleCDO1.paused());
    assertFalse(idleCDO2.paused());
}
```

Concretely: `vm.prank(owner); idleCDO2.setGuardian(guardian2);` where `guardian2 != address(pauser)`, then `pauser.pauseAll()` reverts with `NotAuthorized()` from `IdleCDO._checkOnlyOwnerOrGuardian`, leaving both vaults unpaused.

### Citations

**File:** contracts/HypernativeBatchPauser.sol (L22-33)
```text
  function pauseAll() external {
    if (msg.sender != pauser && msg.sender != owner()) {
      revert NotAllowed();
    }
    uint256 protectedContractLen = protectedContracts.length;
    for (uint256 i = 0; i < protectedContractLen;) {
      IIdleCDO(protectedContracts[i]).emergencyShutdown();
      unchecked {
        ++i;
      }
    }
  }
```

**File:** contracts/HypernativeBatchPauser.sol (L52-59)
```text
  function addProtectedContracts(address[] memory _protectedContracts) public onlyOwner {
    uint256 _protectedContractsLength = _protectedContracts.length;
    for (uint i = 0; i < _protectedContractsLength;) {
      protectedContracts.push(_protectedContracts[i]);
      unchecked {
        ++i;
      }
    }
```

**File:** contracts/IdleCDO.sol (L931-934)
```text
  function emergencyShutdown() external {
    _checkOnlyOwnerOrGuardian();
    _emergencyShutdown(false);
  }
```

**File:** contracts/IdleCDO.sol (L985-987)
```text
  function _checkOnlyOwnerOrGuardian() internal view {
    _checkNotAuthorized(msg.sender != guardian && msg.sender != owner());
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L80-80)
```text
    guardian = _owner;
```

**File:** contracts/IdleCDOCreditVault.sol (L505-508)
```text
  function emergencyShutdown() external {
    _checkOnlyOwnerOrGuardian();
    _emergencyShutdown(false);
  }
```
