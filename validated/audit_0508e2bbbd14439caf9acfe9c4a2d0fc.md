Looking at the actual code in `linkage.rs` and `cyclic_dependencies.rs` to trace the exact logic.

The code is clear enough to make a definitive determination. Let me trace the exact logic.

---

**Core logic in `verify_package_no_cyclic_relationships`** (`linkage.rs` lines 107–135):

```
to_visit_modules = BTreeMap { A, B, Z1 }   // sorted alphabetically
module_map       = clone of above           // snapshot, never mutated

while pop_last():          // processes Z1, then B, then A (reverse alpha)
    visited = verify_module(current)   // DFS looking for path back to `current`
    for k in visited: to_visit_modules.remove(k)
```

**Core logic in `verify_module_impl`** (`cyclic_dependencies.rs` lines 25–65):

```
target = self_id   // the module being checked
detect_cycles(target, dep, visited, deps):
    if cursor == target: return true   // cycle found
    if cursor not in visited:
        visited.insert(cursor)
        recurse into cursor's deps
```

The DFS only detects a path from a dependency **back to `target`**. It does NOT detect cycles among the visited nodes themselves. The `visited` set it returns means "nodes explored while searching for a path back to `target`."

---

**Exact trace of the exploit scenario:**

Package: modules `A`, `B`, `Z1` where `A→B`, `B→A` (cycle), `Z1→A`.

`to_visit_modules` (BTreeMap, sorted): `{A, B, Z1}`. `module_map` = same snapshot.

**Iteration 1:** `pop_last()` → `Z1`
- `verify_module(Z1)`: `target = Z1`
  - DFS from `A`: `A ≠ Z1`, insert `A` into visited → recurse into `B`
  - DFS from `B`: `B ≠ Z1`, insert `B` into visited → recurse into `A`
  - DFS from `A` again: `A ≠ Z1`, but `A` already in visited → stop
  - No path back to `Z1` found → returns `Ok({A, B})`
- Remove `A` and `B` from `to_visit_modules`
- `to_visit_modules` = `{}` (Z1 was popped; A and B removed)

**Loop ends. `A→B→A` cycle was never detected.**

The optimization's invariant is broken: it assumes "if X was visited during Y's DFS and no cycle involving Y was found, X is safe." But the DFS for Y only proves there is no cycle **through Y** — it says nothing about cycles among `{A, B}` themselves.

---

**Is there another guard?**

The only cyclic dependency check during package publication is `verify_package_no_cyclic_relationships` called from `verify_linkage_and_cyclic_checks_for_publication`. [1](#0-0) 

There is no per-module pre-check. The older execution versions (`v0`–`v3`) use a different loader path (`loader.rs`) that calls `cyclic_dependencies` per-module individually, but the current runtime path goes through `linkage.rs` exclusively.

---

**Conclusion:**

The bug is real and the logical error is clear. However, the **impact** must be assessed against the bounty gate:

- Publishing a package with `A→B→A` intra-package bytecode cycle bypasses the verifier.
- At runtime, loading module `A` requires loading `B`, which requires loading `A` — the Move VM module loader has no documented separate cycle guard for this case (the verifier is the intended guard). This risks infinite recursion / stack overflow on any validator or fullnode that loads the package.
- The attacker entry point is a standard package publish transaction — fully public, no privileged access required.
- The impact is a potential validator/fullnode crash from public input, which falls under the active bounty's liveness/node-shutdown class.

This is a genuine verifier bypass reachable from a public package publish transaction.

---

### Title
`verify_package_no_cyclic_relationships` `pop_last` optimization incorrectly prunes intra-package cyclic modules, allowing cycle bypass on publish — (`external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs`)

### Summary
The `pop_last` + visited-pruning optimization in `verify_package_no_cyclic_relationships` removes modules from the to-visit set based on DFS traversals that only check for cycles involving the *current* module. Modules that form a cycle among themselves (e.g., `A→B→A`) are silently pruned from the check when any higher-alphabetical module (e.g., `Z1`) that depends on them is processed first, causing the cycle to escape detection entirely.

### Finding Description
`verify_package_no_cyclic_relationships` builds a `BTreeMap` of all package modules and iterates via `pop_last()` (reverse alphabetical order). [2](#0-1) 

For each popped module it calls `cyclic_dependencies::verify_module`, which performs a DFS looking for a path from any dependency back to `self_id` (the current module as `target`). [3](#0-2) 

The returned `visited` set contains every node explored during that DFS. The caller then removes all of them from `to_visit_modules`: [4](#0-3) 

The invariant assumed is: *"if X was reachable from Y's DFS and no cycle involving Y was found, X is safe."* This is false. The DFS for Y only proves there is no path from Y's dependency subgraph back to Y. It makes no claim about cycles within that subgraph. Modules `A` and `B` forming `A→B→A` are visited (and marked safe) during Y's DFS, then removed from `to_visit_modules` before they are ever individually checked as `target`.

### Impact Explanation
An attacker can craft a package with intra-package bytecode modules containing a mutual dependency cycle (`A→B→A`) plus one higher-alphabetical module (`Z1→A`). The package passes the cyclic dependency verifier and is published on-chain. Any subsequent transaction that causes the Move VM to load module `A` triggers recursive module loading (`A` needs `B`, `B` needs `A`), risking infinite recursion and a stack-overflow crash on validators and fullnodes. This is a liveness/node-shutdown impact reachable from a public package publish transaction.

### Likelihood Explanation
Exploiting this requires crafting raw Move bytecode directly (the Move compiler rejects mutual module dependencies at source level). This is feasible for a motivated attacker with knowledge of the binary format. The entry point is a standard, unauthenticated package publish transaction.

### Recommendation
Remove the visited-pruning optimization entirely, or replace it with a correct one. The correct invariant for skipping a module `X` is: "X was already checked as `target` in a prior iteration and no cycle was found." The current code skips `X` merely because it was *reachable* from another module's DFS, which is insufficient. The simplest fix is to remove lines 131–134 and let every module be checked as `target` exactly once via `pop_last`.

### Proof of Concept

Craft a package with three modules in the same package:
- `A`: `immediate_dependencies = [B]`
- `B`: `immediate_dependencies = [A]`
- `Z1`: `immediate_dependencies = [A]`

Trace through `verify_package_no_cyclic_relationships`:

```
to_visit_modules = {A, B, Z1}
module_map       = {A, B, Z1}   // snapshot

Iteration 1: pop_last() → Z1
  verify_module(Z1, target=Z1):
    DFS(Z1, A, {}):  A≠Z1, visited={A}, recurse into B
    DFS(Z1, B, {A}): B≠Z1, visited={A,B}, recurse into A
    DFS(Z1, A, {A,B}): A≠Z1, A already visited → stop
    → returns Ok({A, B})   // no cycle involving Z1
  remove A, B from to_visit_modules
  to_visit_modules = {}

Loop ends. A→B→A cycle undetected. Package published.
```

Parameterizing: with `k` modules named `Z1..Zk` all depending on `A`, the first `pop_last` (on `Zk`) removes `A` and `B`, and all subsequent iterations are no-ops. The cycle escapes detection for any `k ≥ 1`.

### Citations

**File:** external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs (L87-95)
```rust
    // Now verify the package to publish
    let package_modules = package_to_publish
        .as_modules()
        .into_iter()
        .collect::<Vec<_>>();
    verify_package_valid_linkage(&package_modules, cached_packages, &relocation_map)?;
    verify_package_no_cyclic_relationships(&package_modules, cached_packages, &relocation_map)?;

    Ok(())
```

**File:** external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs (L107-113)
```rust
    let mut to_visit_modules: BTreeMap<_, _> =
        package.iter().map(|m| (m.value.self_id(), m)).collect();
    let module_map = to_visit_modules.clone();

    // Iteratively visit modules, removing them from the to-visit set as we go. If we encounter a
    // cycle an error is returned.
    while let Some((_, module)) = to_visit_modules.pop_last() {
```

**File:** external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs (L131-134)
```rust
        // Remove all visited modules from the to-visit set.
        for k in visited.iter() {
            to_visit_modules.remove(k);
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
