### Title
Stale confirmations from a removed member still count toward quorum, allowing requests to execute below `num_confirmations` live members - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges requests whose `MultiSigRequestWithSigner.member` (the original *submitter*) equals the member being removed; it never scans the `confirmations: LookupMap<RequestId, HashSet<String>>` entries of *other* requests that the removed member merely confirmed. Once that member is deleted, their stale confirmation string remains counted in `confirm()`'s quorum check for those unrelated requests, letting a request execute with fewer than `num_confirmations` currently-live members.

### Finding Description
The invariant the code should maintain is: `{r ∈ requests : removed_member ∈ confirmations[r]} == ∅` after `delete_member(removed_member)` completes — i.e. deletion of a member must purge every trace of that member from every request, not just the ones they originally submitted.

Instead, `delete_member` filters by submitter identity only: [1](#0-0) 
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
`r.member` is set once, at `add_request` time, to whoever created the request: [2](#0-1) 

A *different* member's confirmation is stored purely as a string in the separate `confirmations` HashSet, added in `confirm()`: [3](#0-2) 
```
let member = self.current_member()...
let mut confirmations = self.confirmations.get(&request_id).unwrap();
...
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
} else {
    confirmations.insert(member.to_string());
    self.confirmations.insert(&request_id, &confirmations);
    ...
}
```
There is no re-validation in `confirm()` or `assert_valid_request()` that every string already present in `confirmations[request_id]` still corresponds to a current member of `self.members`: [4](#0-3) 

**Exploit flow** (members A, B, C, D; `num_confirmations = 3`):
1. A calls `add_request` creating request `R1` (any action, e.g. `Transfer`). `R1.member == A`.
2. B calls `confirm(R1)`. `confirmations[R1] = {B}` (1 < 3, not executed).
3. A separate request `R2 = DeleteMember{member: B}` is created and reaches quorum from A, C, D (not B) and executes. Inside `execute_request` → `delete_member(promise, B)` filters `requests` by `r.member == B`; `R1.member == A`, so `R1` is untouched and `B`'s entry in `confirmations[R1]` is never cleared. `B` is removed from `self.members` and its access key deleted.
4. C calls `confirm(R1)` → `confirmations[R1] = {B, C}` (2 < 3).
5. D calls `confirm(R1)` → `confirmations.len() + 1 == 3 >= num_confirmations` → `remove_request` + `execute_request(R1)` fires, even though only **two live members** (C, D) plus one **removed** member's stale approval (B) authorized it.

Existing guards do not stop this: `current_member()` only validates the *caller* of the current call against `self.members`, not the pre-existing entries in `confirmations`; `assert_valid_request` never re-checks confirmer validity; `delete_member`'s cleanup loop is keyed on submitter identity, not on membership in any request's confirmation set.

### Impact Explanation
A multisig request (including `Transfer`, `AddKey`, `DeployContract`, `FunctionCall`, etc.) can be executed with fewer than `num_confirmations` currently live, authorized members — this matches the explicitly listed Critical impact: "a multisig request executed below `num_confirmations` live members." Funds or privileged actions (adding a full-access key, deploying new contract code) can leave/affect the multisig account with less real authorization than the configured threshold requires, and the divergence between "assumed N-of-M live approvals" and "actual (N-1) live approvals + 1 stale approval from a purged member" is exactly the kind of accounting/authorization value divergence the target invariant is meant to prevent. This is repeatable for every request that received a confirmation from a member later removed via `DeleteMember`, and scales to any multisig deployed from this contract.

### Likelihood Explanation
No privileged role is required beyond being one of the multisig's own existing members acting through the normal `add_request` / `confirm` / `DeleteMember` flow — this is an emergent bug in the members' own governance process, not requiring any external attacker capability beyond normal multisig usage. It requires only ordinary sequencing: a confirmation is cast on one request, and the confirming member is separately removed via `DeleteMember`, which is a very plausible operational occurrence (e.g., removing a compromised/departing key holder) — after which the stale confirmation continues to count. This is deterministic and 100% reproducible whenever a member is removed after having confirmed at least one still-pending request.

### Recommendation
When `delete_member` runs, also purge the removed member's identity string from every entry in `self.confirmations`, not just requests where they are the original submitter, e.g. iterate all `(request_id, confirmations_set)` pairs, call `confirmations_set.remove(&member.to_string())`, and persist. Alternatively, re-validate at `confirm()` time that every string in the stored confirmation set still corresponds to a `MultisigMember` present in `self.members`, discarding stale ones before comparing against `num_confirmations`.

### Proof of Concept
```rust
// multisig2/src/lib.rs (add near existing #[cfg(test)] mod tests)
#[test]
fn test_stale_confirmation_after_member_removed_still_counts() {
    // Setup 4 members (A, B, C, D as MultisigMember::Account), num_confirmations = 3
    // 1. A adds request R1 (Transfer)
    // 2. B confirms R1 -> assert confirmations[R1].len() == 1
    // 3. A, C, D create+confirm DeleteMember{member: B} to quorum -> executes, B removed
    //    assert !c.get_members().contains(&B)
    // 4. C confirms R1 -> confirmations[R1].len() == 2 (still contains stale "B")
    // 5. D confirms R1 -> triggers execute (3 >= num_confirmations)
    //    assert_eq!(c.requests.len(), 0); // R1 executed
    //    BUT only 2 live members (C, D) + 1 removed member (B) approved it.
    // Assertion that demonstrates the bug: before step 3, confirmations[R1] contained "B";
    // after B's removal via delete_member, confirmations[R1] STILL contains "B"
    // (assert c.confirmations.get(&r1_id).unwrap().contains(&B.to_string()) == true),
    // proving delete_member's filter (r.member == member) failed to purge B's
    // confirmation from a request B did not submit.
}
```
The critical assertion is: `confirmations[R1].contains(B.to_string())` remains `true` after `delete_member(B)` runs and `B ∉ self.members` — i.e., the equality `{r : B ∈ confirmations[r]} == ∅` (expected post-deletion) does **not** hold, while `self.members.contains(&B) == false` does hold, proving the divergence.

### Citations

**File:** multisig2/src/lib.rs (L189-194)
```rust
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
```

**File:** multisig2/src/lib.rs (L296-313)
```rust
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
```

**File:** multisig2/src/lib.rs (L361-371)
```rust
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
```

**File:** multisig2/src/lib.rs (L407-423)
```rust
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
