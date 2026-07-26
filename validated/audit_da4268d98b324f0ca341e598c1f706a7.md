The logic flaw described is real. Let me trace through the exact code to confirm.

## Code Analysis

**`verify_package_no_cyclic_relationships` (linkage.rs:102-138)** [1](#0-0) 

The loop pops modules from `to_visit_modules` and calls `cyclic_dependencies::verify_module`, then removes the returned `visited` set from `to_visit_modules`.

**`cyclic_dependencies::verify_module` (cyclic_dependencies.rs:13-65)** [2](#0-1) 

The inner `detect_cycles` function takes a fixed `target` (the root module's self_id) and checks whether any path from `cursor` leads back to `target`. The `visited` set it accumulates is all nodes traversed during that DFS.

## The Actual Flaw

For a package with modules A, B, C where A→B, B→C, C→B:

**When A is popped first (BTreeMap `pop_last` order):**

`verify_module(A)` runs `detect_cycles(target=A, cursor=B, ...)`:
- Visits B (B ≠ A, insert into visited)
- Visits C (C ≠ A, insert into visited)
- Tries B again (already in visited → `is_new = false`, stops)
- Returns `Ok(false)` — no cycle back to A found

Returned `visited = {B, C}`. [3](#0-2) 

**Back in `verify_package_no_cyclic_relationships`:** [4](#0-3) 

B and C are removed from `to_visit_modules`. The loop ends. B is **never checked as a root**.

**If B were checked as a root:**
`detect_cycles(target=B, cursor=C, ...)` → visits C → tries B → B == target → returns `Ok(true)` → **CYCLE DETECTED**.

The optimization assumes: "if A's DFS visited B, B needs no further checking." This is wrong. A's DFS only checks for paths back to A, not for cycles among B's descendants.

## Impact Assessment

The verifier bypass is real and demonstrable. However, the bounty-qualifying impact requires careful evaluation:

- **Verifier bypass**: confirmed — a package with a B→C→B cycle can be published.
- **Runtime crash claim**: uncertain. The Move VM's module loader may have its own "currently loading" guard that prevents infinite recursion when it encounters a cycle. If it does, the published package simply fails to load gracefully rather than crashing. If it does not (relying entirely on the verifier), a stack overflow crash on any fullnode that loads the package is possible — qualifying as a **Low** impact (public-input fullnode crash).
- **Fund theft / state corruption / permanent lock**: not directly caused by this flaw. A cyclic package cannot be executed, so it cannot manipulate state or steal funds.
- **The "High" scope claim in the question is overstated.** The impact ceiling is Low (fullnode crash), not High.

The flaw is real and the proof-of-concept scenario is valid, but the impact lands at **Low** under the active bounty gate, not High.

---

### Title
Incorrect visited-set optimization in `verify_package_no_cyclic_relationships` allows publication of packages with intra-package cyclic module dependencies — (`external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs`)

### Summary
`verify_package_no_cyclic_relationships` removes modules from the to-visit set based on nodes traversed during another module's DFS. Because `cyclic_dependencies::verify_module` only detects cycles back to its own root, nodes visited during A's DFS are not guaranteed to be cycle-free from their own perspective. A B→C→B cycle is missed when B is visited (but not rooted) during A's DFS.

### Finding Description
In `linkage.rs:113-134`, the while loop pops a module, calls `cyclic_dependencies::verify_module`, and removes all returned visited nodes from `to_visit_modules`. The `detect_cycles` inner function in `cyclic_dependencies.rs:32-55` uses a fixed `target` equal to the root module's ID and only returns `true` when a path leads back to that target. The `visited` set it returns is all nodes traversed — not all nodes proven cycle-free. Removing these from `to_visit_modules` skips them as future roots, leaving intra-subgraph cycles undetected.

### Impact Explanation
A package publisher can craft a three-module package (A→B, B→C, C→B) and publish it successfully. At minimum this is a verifier invariant violation. If the VM loader lacks its own cycle guard, any fullnode loading the package crashes (Low bounty impact).

### Likelihood Explanation
Requires a deliberate package construction. Any package publisher can trigger this.

### Recommendation
Do not remove modules from `to_visit_modules` based on the visited set of another module's DFS. Either: (a) remove only the module that was just popped (not its transitive visited set), accepting O(n) DFS calls; or (b) implement a proper SCC algorithm (Tarjan/Kosaraju) that correctly identifies all cycles in one pass.

### Proof of Concept
Construct a Move package with three modules where A imports B, B imports C, C imports B. Publish the package. Assert that `verify_package_no_cyclic_relationships` returns `Ok(())` despite the B→C→B cycle. Then verify that calling `cyclic_dependencies::verify_module` directly on B detects the cycle.

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

**File:** external-crates/move/crates/move-bytecode-verifier/src/cyclic_dependencies.rs (L25-65)
```rust
fn verify_module_impl<D>(
    module: &CompiledModule,
    imm_deps: D,
) -> PartialVMResult<BTreeSet<ModuleId>>
where
    D: Fn(&ModuleId) -> PartialVMResult<Vec<ModuleId>>,
{
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

    let self_id = module.self_id();
    let mut visited = BTreeSet::new();
    for dep in module.immediate_dependencies() {
        if detect_cycles(&self_id, &dep, &mut visited, &imm_deps)? {
            return Err(PartialVMError::new(StatusCode::CYCLIC_MODULE_DEPENDENCY));
        }
    }

    Ok(visited)
```
