## Analysis

### Step 1: Evaluate the Specific Claimed Mechanism (External Module E)

The question claims external module E's `immediate_dependencies()` can return M2's `ModuleId` (an intra-package module of the new publish TX).

**This specific mechanism is not feasible.** In the closure passed to `cyclic_dependencies::verify_module`, when `original_module_id` refers to an external module E, the code looks up E in `cached_packages` and returns `E.value.immediate_dependencies()` — the actual bytecode-declared dependencies of E. [1](#0-0) 

E is an already-published module. M2 is in the package being published right now — it did not exist when E was published. Sui package addresses are derived from transaction digests, so the attacker cannot pre-arrange for E to declare a dependency on M2. The external-E scenario is a dead end.

---

### Step 2: Evaluate the Underlying Code Logic

Despite the wrong mechanism, the optimization itself is genuinely broken. Here is the exact flaw:

`verify_package_no_cyclic_relationships` builds `to_visit_modules` from all package modules, then iterates via `pop_last()` (lexicographic descending order). For each module M1 it calls `cyclic_dependencies::verify_module`, collects the returned `visited` set, and **removes every visited module from `to_visit_modules`**. [2](#0-1) 

The assumption is: "if M2 was visited during M1's DFS without a cycle being detected, M2 needs no independent check." This assumption is **wrong**.

`detect_cycles` uses `target = M1.self_id()` and only returns `true` when the DFS reaches M1 again. It uses the `visited` set to avoid re-traversing nodes — but this means a cycle entirely within {M2, M3} that does not pass through M1 is silently swallowed. [3](#0-2) 

---

### Step 3: Concrete Exploit Trace (Purely Intra-Package)

Craft a package with three modules named so that M1 sorts last lexicographically:

| Module | Name | Depends on |
|--------|------|------------|
| M1 | `"zzz"` | `"aaa"` (M2) |
| M2 | `"aaa"` | `"bbb"` (M3) |
| M3 | `"bbb"` | `"aaa"` (M2) ← hidden cycle |

`to_visit_modules` = `{"aaa": M2, "bbb": M3, "zzz": M1}`

**Iteration 1** — `pop_last()` yields `("zzz", M1)`, target = `"zzz"`:
- cursor=`"aaa"` ≠ target, insert → visited=`{"aaa"}`, is_new=true
  - cursor=`"bbb"` ≠ target, insert → visited=`{"aaa","bbb"}`, is_new=true
    - cursor=`"aaa"` ≠ target, `visited.insert("aaa")` → **is_new=false**, no recursion → `Ok(false)`
  - `Ok(false)`
- `Ok(false)` — no cycle detected (correct: M1 has no self-cycle)

`visited = {"aaa","bbb"}` → both removed from `to_visit_modules` → loop ends.

**If M2 were independently checked** (target = `"aaa"`):
- cursor=`"bbb"` ≠ target, insert, is_new=true
  - cursor=`"aaa"` == target → `Ok(true)` → **CYCLIC_MODULE_DEPENDENCY**

The cycle M2→M3→M2 is never detected. The package passes verification and is published.

---

### Step 4: Impact

A published package with a hidden intra-package cycle:
- Violates Move's core invariant that module dependencies form a DAG
- Enables cross-module mutual recursion (M2 calls M3 calls M2…), which Move's design explicitly forbids
- Corrupts module initialization order if both M2 and M3 carry `init` functions, potentially allowing one module's `init` to execute against another's uninitialized state
- Undermines type-system soundness properties that are proven only under the DAG assumption

This is reachable via a standard unprivileged publish TX. The attacker controls module names and dependency declarations entirely.

---

### Title
Cyclic-Dependency Skip in `verify_package_no_cyclic_relationships` Allows Publishing a Package with a Hidden Intra-Package Module Cycle — (`external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs`)

### Summary
`verify_package_no_cyclic_relationships` removes intra-package modules from `to_visit_modules` after they appear in the `visited` set of another module's DFS. Because `detect_cycles` only searches for cycles that pass through the current DFS root (target), a cycle entirely among non-root modules is never detected, and those modules are never independently checked.

### Finding Description
The optimization at lines 131–134 of `linkage.rs` is unsound. `cyclic_dependencies::verify_module` returns the set of all nodes reachable from M1's dependencies during a DFS whose sole cycle-detection criterion is "does the path return to M1?" A cycle M2→M3→M2 that does not involve M1 causes M3 to be re-encountered with `is_new=false`, silently terminating that branch. Both M2 and M3 end up in `visited`, are removed from `to_visit_modules`, and are never given their own independent cycle check. [4](#0-3) [5](#0-4) 

### Impact Explanation
A package with a hidden cycle between two or more non-root modules passes `verify_linkage_and_cyclic_checks_for_publication` and is published on-chain. This constitutes **harmful smart-contract behavior**: Move's type-system soundness, module initialization correctness, and reentrancy-prevention guarantees all depend on the DAG invariant. Mutual recursion across modules and undefined `init` ordering are direct consequences.

### Likelihood Explanation
Fully attacker-controlled. The attacker chooses module names to control `pop_last()` ordering and writes the dependency declarations. No privileged access, no malicious validator, no leaked key is required — only a standard publish transaction.

### Recommendation
Remove the `to_visit_modules.remove(k)` optimization entirely and verify every module in the package independently:

```rust
for module in package {
    cyclic_dependencies::verify_module(&module.value, |id| { ... })?;
}
```

Alternatively, replace the optimization with a correct whole-graph cycle check (e.g., Kahn's algorithm or a proper DFS with a "currently on stack" color, applied once over the full intra-package dependency graph).

### Proof of Concept

```rust
// Pseudocode: craft a 3-module package
// Module "zzz": use aaa::M2;   (depends on "aaa")
// Module "aaa": use bbb::M3;   (depends on "bbb")
// Module "bbb": use aaa::M2;   (depends on "aaa" — cycle)

let package = build_package(&[
    ("zzz", vec!["aaa"]),
    ("aaa", vec!["bbb"]),
    ("bbb", vec!["aaa"]),  // hidden cycle
]);

// verify_package_no_cyclic_relationships returns Ok(()) — cycle missed
assert!(verify_package_no_cyclic_relationships(&package, ...).is_ok());

// But verifying "aaa" alone catches it:
assert!(cyclic_dependencies::verify_module(&module_aaa, deps_fn).is_err());
```

**Note on the question's framing:** The external-module-E mechanism described in the question is not feasible (E cannot depend on a module that does not yet exist). The real attack vector is purely intra-package as shown above, and it is sufficient to trigger the bug.

### Citations

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

**File:** external-crates/move/crates/move-bytecode-verifier/src/cyclic_dependencies.rs (L32-55)
```rust
    fn detect_cycles<D>(
        target: &ModuleId,
        cursor: &ModuleId,
        visited: &mut BTreeSet<ModuleId>,
        deps: &D,
    ) -> PartialVMResult<bool>
    where
        D: Fn(&ModuleId) -> PartialVMResult<Vec<ModuleId>>,
    {
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
    }
```
