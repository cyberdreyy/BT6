The code is unambiguous. Let me confirm the exact behavior at lines 445–449 and the test that explicitly validates the "same count, different identity" case.

The code is definitive. Here is the analysis:

---

### Title
Friend Module Identity Not Checked During `DepOnly`/`Additive` Package Upgrades — (`external-crates/move/crates/move-binary-format/src/compatibility.rs`)

### Summary

`InclusionCheck::check_with_mode` compares only the **count** of declared friend modules between old and new normalized modules. It never compares friend module addresses or names. A package publisher can perform a `DepOnly` (`Equal`) or `Additive` (`Subset`) upgrade that silently replaces every declared friend with a completely different module of the same count, bypassing the access-control invariant that `public(friend)` functions are restricted to specific trusted callers.

### Finding Description

In `InclusionCheck::check_with_mode`, the friend check is:

```rust
// friend checks, keeping in line with the previous implementation only checking for length differences.
// will need followup work and a protocol version for more detailed friend checks.
if old_module.friends.len() != new_module.friends.len() {
    context.friend_mismatch(old_module.friends.len(), new_module.friends.len());
}
``` [1](#0-0) 

`friend_mismatch` in `InclusionCheckExecutionMode` only clears `is_equal`, not `is_subset`:

```rust
fn friend_mismatch(&mut self, _old_count: usize, _new_count: usize) {
    self.is_equal = false;
}
``` [2](#0-1) 

Consequences:
- **`InclusionCheck::Equal` (`DepOnly`)**: passes as long as the friend count is unchanged, regardless of which modules are listed.
- **`InclusionCheck::Subset` (`Additive`)**: friend count changes are completely ignored — `friend_mismatch` never sets `is_subset = false`, so any friend list is accepted.

The existing test suite **explicitly encodes and asserts this behavior** as correct:

```rust
// 2 friends, changed to 2 different friends
assert!(InclusionCheck::Subset.check(&m1, &m4).is_ok());
// ...
// 2 friends, changed to 2 different friends
assert!(InclusionCheck::Equal.check(&m1, &m4).is_ok());
```

where `m1` has `friend_modules[0]` and `friend_modules[1]`, and `m4` has `friend_modules[2]` and `friend_modules[3]` (all with distinct random `AccountAddress` values). [3](#0-2) 

The upgrade policy is enforced here:

```rust
UpgradePolicy::DepOnly => InclusionCheck::Equal.check_with_mode::<CliInclusionCheckMode>(...)
UpgradePolicy::Additive => InclusionCheck::Subset.check_with_mode::<CliInclusionCheckMode>(...)
``` [4](#0-3) 

### Impact Explanation

Move's `public(friend)` visibility is an access-control primitive. Only modules explicitly listed as friends can call `public(friend)` functions. If a package uses `public(friend)` to gate privileged operations (e.g., minting, transferring, modifying shared state), and users trust that package because its upgrade policy is locked to `DepOnly` or `Additive`, a malicious publisher can:

1. Perform a `DepOnly` upgrade that swaps the friend list for attacker-controlled module addresses (same count).
2. Deploy those attacker-controlled modules.
3. Call the previously-restricted `public(friend)` functions to steal funds or corrupt shared state.

This is "harmful smart-contract behavior" — the `DepOnly`/`Additive` upgrade policies are supposed to be a verifiable trust guarantee to users, and this bug silently voids that guarantee for friend-gated access control.

### Likelihood Explanation

Package publishers are explicitly within the attacker model. The attack requires only a standard package upgrade transaction — a public, unprivileged entrypoint. No validator collusion, leaked keys, or governance quorum is needed. The only precondition is that the target package uses `public(friend)` for access control and has been locked to `DepOnly` or `Additive` upgrades as a trust signal.

### Recommendation

Replace the length-only comparison with a full set comparison of friend module identities (address + module name). For `Equal`, the sets must be identical. For `Subset`, the old friend set must be a subset of the new one (or the sets must be equal, depending on intended semantics). A protocol version gate is already anticipated by the in-code comment.

### Proof of Concept

```rust
// Build two normalized modules with the same number of friends but different addresses.
// m1: friends = [0x1::M1, 0x2::M2]
// m4: friends = [0x3::M3, 0x4::M4]
// InclusionCheck::Equal.check(&m1, &m4) returns Ok(()) — no error.
// This is confirmed by the existing test `test_friend_linking` at line 1408.
assert!(InclusionCheck::Equal.check(&m1, &m4).is_ok()); // BUG: should be Err
```

The existing test at line 1408 of `compatibility_tests.rs` is itself the proof of concept — it asserts the buggy behavior as expected. [5](#0-4) 

---

**Verdict**: This is a real, demonstrable gap in the upgrade compatibility checker. The friend identity check is entirely absent. Whether it meets the active bounty threshold depends on whether the impact classification of "harmful smart-contract behavior via unauthorized `public(friend)` access after upgrade" is accepted — the code path is real and the bypass is unconditional.

### Citations

**File:** external-crates/move/crates/move-binary-format/src/compatibility.rs (L445-449)
```rust
        // friend checks, keeping in line with the previous implementation only checking for length differences.
        // will need followup work and a protocol version for more detailed friend checks.
        if old_module.friends.len() != new_module.friends.len() {
            context.friend_mismatch(old_module.friends.len(), new_module.friends.len());
        }
```

**File:** external-crates/move/crates/move-binary-format/src/inclusion_mode.rs (L131-133)
```rust
    fn friend_mismatch(&mut self, _old_count: usize, _new_count: usize) {
        self.is_equal = false;
    }
```

**File:** external-crates/move/crates/move-binary-format/src/unit_tests/compatibility_tests.rs (L1369-1408)
```rust
    // 2 friends, different from m1
    let m4 = mk_module_with_defs(
        pool,
        vec![],
        vec![],
        vec![],
        vec![friend_modules[2].clone(), friend_modules[3].clone()],
    );

    // Subset, all changes are allowed
    // same module, no friends
    assert!(InclusionCheck::Subset.check(&m0, &m0).is_ok());
    // start with empty and add friend
    assert!(InclusionCheck::Subset.check(&m0, &m1).is_ok());
    // start with two, keep the two and add one more
    assert!(InclusionCheck::Subset.check(&m1, &m2).is_ok());
    // start with two, remove them
    assert!(InclusionCheck::Subset.check(&m3, &m0).is_ok());
    // start with three remove one
    assert!(InclusionCheck::Subset.check(&m2, &m1).is_ok());
    // change order
    assert!(InclusionCheck::Subset.check(&m1, &m3).is_ok());
    // 2 friends, changed to 2 different friends
    assert!(InclusionCheck::Subset.check(&m1, &m4).is_ok());

    // Equal, can only keep the same number of friends
    // same module, no friends
    assert!(InclusionCheck::Equal.check(&m0, &m0).is_ok());
    // start with empty and add friends
    assert!(InclusionCheck::Equal.check(&m0, &m1).is_err());
    // start with two, keep the two and add one more
    assert!(InclusionCheck::Equal.check(&m1, &m2).is_err());
    // start with two, remove them
    assert!(InclusionCheck::Equal.check(&m3, &m0).is_err());
    // start with three remove one
    assert!(InclusionCheck::Equal.check(&m2, &m1).is_err());
    // change order
    assert!(InclusionCheck::Equal.check(&m1, &m3).is_ok());
    // 2 friends, changed to 2 different friends
    assert!(InclusionCheck::Equal.check(&m1, &m4).is_ok());
```

**File:** crates/sui/src/upgrade_compatibility/mod.rs (L830-837)
```rust
        UpgradePolicy::DepOnly => InclusionCheck::Equal.check_with_mode::<CliInclusionCheckMode>(
            &Module::new(pool, existing_module, /* include code */ true),
            &Module::new(pool, new_module, /* include code */ true),
        ),
        UpgradePolicy::Additive => InclusionCheck::Subset.check_with_mode::<CliInclusionCheckMode>(
            &Module::new(pool, existing_module, /* include code */ true),
            &Module::new(pool, new_module, /* include code */ true),
        ),
```
