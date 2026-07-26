I have all the information needed. Let me trace through the exact algorithm carefully before rendering a verdict.

I now have a complete picture. Let me do the full algorithmic trace and render a verdict.

---

## Full Algorithmic Trace

**Scenario**: Package at address `0xA` with modules `X`, `Y`, `Z` where:
- Z depends on Y and X
- Y depends on X
- X depends on Y (creating Y↔X cycle)

**`verify_package_no_cyclic_relationships`** builds a `BTreeMap` keyed by `self_id()`. BTreeMap sorts lexicographically, so order is: `X < Y < Z`.

`pop_last()` → **Z** is selected first.

**`cyclic_dependencies::verify_module(Z)`** is called. `target = Z`, `visited = {}`.

Z's immediate deps: `[Y, X]`.

```
detect_cycles(target=Z, cursor=Y, visited={})
  Y ≠ Z
  visited.insert(Y) → is_new=true, visited={Y}
  Y's deps: [X]
    detect_cycles(target=Z, cursor=X, visited={Y})
      X ≠ Z
      visited.insert(X) → is_new=true, visited={Y,X}
      X's deps: [Y]
        detect_cycles(target=Z, cursor=Y, visited={Y,X})
          Y ≠ Z
          visited.insert(Y) → is_new=FALSE  ← Y already present
          skip recursion
          return false
      return false
    return false
detect_cycles(target=Z, cursor=X, visited={Y,X})
  X ≠ Z
  visited.insert(X) → is_new=FALSE  ← X already present
  skip recursion
  return false
```

`verify_module(Z)` returns **`Ok({Y, X})`** — no cycle detected.

Back in `verify_package_no_cyclic_relationships`: Y and X are removed from `to_visit_modules`. The map is now empty. Loop ends. **Returns `Ok(())`.**

The Y↔X cycle is silently accepted.

---

## Why the Existing Test Does NOT Cover This

The existing test `publish_cyclic_modules_partial_package_cycle` uses **A** (no deps), **B→C**, **C→B**:
- `pop_last()` → C
- `detect_cycles(target=C, cursor=B, ...)` → B's dep is C → `detect_cycles(target=C, cursor=C, ...)` → `cursor == target` → **returns true** → error raised.

That test works because C is **inside** the cycle. In the question's scenario, Z is **outside** the Y↔X cycle, so the DFS from Z never finds a path back to Z, and Y/X are pruned before they can be used as starting targets.

---

## Publication Path Confirmed [1](#0-0) 

`validate_for_publish` → `validate_package` (explicitly defers cycle check per the comment "We don't check for cycles inside of the package just yet") → `verify_linkage_and_cyclic_checks_for_publication` → `verify_package_no_cyclic_relationships`. This is the **sole** intra-package cycle gate in the new runtime. [2](#0-1) 

The `latest` execution adapter calls `validate_package` directly: [3](#0-2) 

---

## Verdict

### Title
Intra-Package Cycle Bypass in `verify_package_no_cyclic_relationships` Allows Cyclic Package Publication Leading to Permanent Fund Lock — (`external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs`)

### Summary
`verify_package_no_cyclic_relationships` uses `pop_last()` on a `BTreeMap` to select the alphabetically-last module as the DFS root, then removes all modules visited during that DFS from the pending set. Because `cyclic_dependencies::verify_module` only detects cycles that pass through the starting module (the `target`), a cycle among non-last modules (Y↔X) that is reachable from the last module (Z) is traversed but never flagged, and Y and X are then pruned from the pending set before they can serve as independent DFS roots. The cycle is silently accepted and the package is published.

### Finding Description

`verify_package_no_cyclic_relationships` iterates by calling `pop_last()` on a `BTreeMap<ModuleId, &Module>`: [4](#0-3) 

`cyclic_dependencies::verify_module` performs a DFS whose termination condition is `cursor == target` — it only detects cycles that loop back to the **starting** module: [5](#0-4) 

The returned `BTreeSet<ModuleId>` (`visited`) contains every node the DFS touched. These are then removed from `to_visit_modules`: [6](#0-5) 

When Z (last alphabetically) depends on both Y and X, and Y↔X form a cycle, the DFS from Z visits Y and X (marking them in `visited`) but never finds a path back to Z. Both Y and X are then removed from `to_visit_modules`. They are never used as DFS roots themselves. The Y↔X cycle is never detected.

### Impact Explanation

A package publisher (ordinary SUI holder) can craft raw bytecode for a three-module package with this topology and submit a `Publish` transaction. The verifier accepts it. The package is written to the object store. Any subsequent transaction that calls a function in Y or X causes the VM loader to attempt to load Y→X→Y recursively, resulting in an abort on every invocation. If users have deposited SUI or other coins into shared objects managed by Y or X (e.g., a staking vault, a DEX pool, a bridge escrow), those funds become permanently unclaimable. No privileged actor is required; the attacker is a standard package publisher.

### Likelihood Explanation

The technical exploit is straightforward: craft bytecode directly (bypassing the Move compiler's own cycle rejection) and submit a `Publish` PTB. The social-engineering step (convincing users to deposit funds) is the only friction, placing likelihood at **Medium**.

### Recommendation

Replace the "remove visited nodes" optimization with a per-module independent DFS. Every module in the package must be used as a `target` exactly once, regardless of whether it was visited during a prior DFS:

```rust
// Correct: check every module independently as its own target
for module in package {
    cyclic_dependencies::verify_module(&module.value, |id| { ... })?;
}
```

Alternatively, implement a proper SCC (Tarjan/Kosaraju) algorithm over the full intra-package module graph instead of the current per-root reachability check.

Add a regression test mirroring the exact topology: Z→Y, Z→X, Y→X, X→Y, asserting `CYCLIC_MODULE_DEPENDENCY` is returned. [7](#0-6) 

### Proof of Concept

```
Package at 0xA:
  module 0xA::Z { use 0xA::Y; use 0xA::X; }
  module 0xA::Y { use 0xA::X; }
  module 0xA::X { use 0xA::Y; }   // Y↔X cycle

BTreeMap order: X < Y < Z
pop_last() → Z
verify_module(Z): DFS visits Y, X; no path back to Z → Ok({Y,X})
Remove Y, X from to_visit_modules → map empty
Loop ends → Ok(())   ← cycle missed, package published

Subsequent call to 0xA::Y::withdraw(coin):
  Loader: load Y → needs X → needs Y → abort (CYCLIC_MODULE_DEPENDENCY or stack overflow)
  User's deposited SUI in Y's shared object: permanently locked.
```

### Citations

**File:** external-crates/move/crates/move-vm-runtime/src/validation/mod.rs (L56-70)
```rust
    let validated_package = validate_package(natives, vm_config, package)?;

    if validated_package.original_id != original_id {
        return Err(partial_vm_error!(
            UNKNOWN_INVARIANT_VIOLATION_ERROR,
            "Mismatched original package IDs: given '{}', found '{}'",
            original_id,
            validated_package.original_id
        )
        .finish(Location::Package(validated_package.version_id)));
    }

    // Now verify linking on-the-spot to make sure that the current package links correctly in
    // the supplied linking context.
    verify_linkage_and_cyclic_checks_for_publication(&validated_package, &dependencies)?;
```

**File:** external-crates/move/crates/move-vm-runtime/src/validation/mod.rs (L98-103)
```rust
    let pkg = deserialization::translate::package(vm_config, package)?;

    // NB: We don't check for cycles inside of the package just yet since we may need to load
    // further packages.
    let pkg = verification::translate::package(natives, vm_config, pkg)?;
    Ok(pkg)
```

**File:** sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs (L1125-1135)
```rust
        let vm = self
            .env
            .vm
            .validate_package(
                data_store,
                *package_id,
                serialized_pkg,
                &mut SuiGasMeter(self.gas_charger.move_gas_status_mut()),
                self.native_extensions.clone(),
            )
            .map_err(|e| self.env.convert_linked_vm_error(e, linkage))?;
```

**File:** external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs (L107-135)
```rust
    let mut to_visit_modules: BTreeMap<_, _> =
        package.iter().map(|m| (m.value.self_id(), m)).collect();
    let module_map = to_visit_modules.clone();

    // Iteratively visit modules, removing them from the to-visit set as we go. If we encounter a
    // cycle an error is returned.
    while let Some((_, module)) = to_visit_modules.pop_last() {
        let visited = cyclic_dependencies::verify_module(&module.value, |original_module_id| {
            let module = if let Some(bundled) = module_map.get(original_module_id) {
                Some(**bundled)
            } else {
                let version_id = relocation_map
                    .get(original_module_id.address())
                    .ok_or_else(|| partial_vm_error!(MISSING_DEPENDENCY))?;
                cached_packages
                    .get(version_id)
                    .and_then(|p| p.modules.get(&original_module_id.to_owned()))
            };

            module
                .map(|m| m.value.immediate_dependencies())
                .ok_or_else(|| partial_vm_error!(MISSING_DEPENDENCY))
        })?;

        // Remove all visited modules from the to-visit set.
        for k in visited.iter() {
            to_visit_modules.remove(k);
        }
    }
```

**File:** external-crates/move/crates/move-bytecode-verifier/src/cyclic_dependencies.rs (L41-54)
```rust
        if cursor == target {
            return Ok(true);
        }

        let is_new = visited.insert(cursor.clone());
        if is_new {
            for dep in deps(cursor)? {
                if detect_cycles(target, &dep, visited, deps)? {
                    return Ok(true);
                }
            }
        }

        Ok(false)
```

**File:** external-crates/move/crates/move-vm-runtime/src/unit_tests/loader_tests.rs (L1241-1259)
```rust
#[test]
fn publish_cyclic_modules_partial_package_cycle() {
    let data_store = InMemoryStorage::new();
    let mut adapter = Adapter::new(data_store);

    // Package has three modules: A (no cycle), B <-> C (cycle between them).
    // A is fine on its own -- the cycle only exists between B and C.
    // This ensures cycle detection checks all modules, not just the first.
    let module_a = named_empty_module(ADDR2, "A".to_string());
    let module_b =
        empty_module_with_dependencies(ADDR2, "B".to_string(), (ADDR2, vec!["C".to_string()]));
    let module_c =
        empty_module_with_dependencies(ADDR2, "C".to_string(), (ADDR2, vec!["B".to_string()]));

    let pkg =
        StoredPackage::from_modules_for_testing(ADDR2, vec![module_a, module_b, module_c]).unwrap();
    let err = adapter.publish_package_with_error(pkg);
    assert_eq!(err.major_status(), StatusCode::CYCLIC_MODULE_DEPENDENCY);
}
```
