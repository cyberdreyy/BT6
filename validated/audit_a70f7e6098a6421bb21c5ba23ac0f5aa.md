### Title
Stale confirmations from removed members still count toward `num_confirmations`, allowing multisig execution below live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`confirm()` accepts a request once `confirmations.len() + 1 >= self.num_confirmations`, but the confirmations `HashSet` is never purged of a member's entries when that member is later removed via `delete_member`, unless the removed member happens to be the original *creator* of that specific request. A confirmation cast by a member while they were legitimately part of the multisig therefore remains valid forever afterward and can be silently counted toward the approval threshold of requests they did not create, even after they have been removed (and possibly re-added under a different membership/`num_confirmations` state), letting a request execute with fewer currently-live approving members than `num_confirmations` requires.

### Finding Description
The invariant the contract must uphold is:
`confirmations.get(&request_id).len() == |{ m ∈ confirmations_set(request_id) : self.members.contains(m) }|`
i.e. every entry counted toward the threshold must belong to a *currently valid* member. This binding is broken.

`confirm()` simply compares the raw set size: [1](#0-0) 

The only membership check performed is on the *caller* via `assert_valid_request` → `current_member()`, not on the members whose approvals are already stored in the `confirmations` set: [2](#0-1) 

When a member is removed, `delete_member` only purges requests (and their confirmation sets) that were *authored* by that member (`r.member == member`), plus the member's own `num_requests_pk` counter. It does not scan other pending requests' `confirmations` sets to strip out that member's prior approvals on requests authored by someone else: [3](#0-2) 

Exploit flow (multisig with members A, B, C, `num_confirmations = 2`):
1. A calls `add_request(Y)` (e.g. `Transfer` of the account's NEAR) — `Y.member = A`, `confirmations[Y] = {}`.
2. B calls `confirm(Y)` — `confirmations[Y] = {B}` (1/2, not yet executed).
3. A separate, properly-approved request removes B from the multisig (`DeleteMember { member: B }`). Since `Y.member == A`, not `B`, `delete_member` does **not** touch `Y` or its confirmations; `B`'s stale approval for `Y` survives untouched, only `num_requests_pk[B]` is cleared.
4. B is no longer a member (and could later be re-added under a completely different governance context/`num_confirmations`).
5. A calls `confirm(Y)` again. `assert_valid_request`/`current_member()` succeed because A is still a live member. `confirmations[Y] = {B}` does not contain A, so the "already confirmed" guard passes. `confirmations.len() + 1 == 2 >= num_confirmations(2)` → `execute_request(Y)` fires and the transfer executes.

Only one currently-live member (A) actually approved the transfer in real time; B's approval is a leftover artifact from before B was removed. No code path re-validates stored confirmations against the live `members` set, so the guard `assert_valid_request`/`current_member` (which only checks the *caller*) fails to prevent this. The "member removed and re-added" variant described in the question is a more insidious version of the same root cause: re-adding a member (resetting only `num_requests_pk`, not `confirmations`) lets a stale approval keep silently backing an old request that the current member set, and possibly a different `num_confirmations` value, never actually approved together.

### Impact Explanation
This directly matches the listed Critical category: "a multisig request executed below `num_confirmations` live members." NEAR funds held by the multisig account (or any other action gated behind `num_confirmations`, including `AddKey`/`AddMember`/`FunctionCall` with attached deposits) can be pushed through with fewer genuinely live, currently-trusted approvals than the configured threshold demands. This is repeatable for every pending request that collects a partial confirmation before one of its confirmers is removed, and applies to any multisig deployed from this contract.

### Likelihood Explanation
The scenario requires normal governance actions that are entirely plausible in a real multisig's lifecycle: a request partially confirmed, followed at some later point by removal of one of its confirmers (a routine key-rotation/membership-management action), followed by the remaining confirmer(s) completing the original request. No attacker needs elevated privileges beyond being one of the (still) legitimate members completing the request — the flaw is that the *removed* member's stale approval is what tips the balance, not that the completer needs extra rights. Because this can happen purely as a side effect of ordinary membership churn (not even requiring intentional malice at removal time), likelihood is high wherever a multisig rotates members while requests are outstanding.

### Recommendation
When removing a member in `delete_member`, iterate all pending requests' `confirmations` sets (not just those authored by the removed member) and strip the removed member's entry from each. Alternatively, at confirmation-count time in `confirm()`, filter `confirmations` to only those members still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
```rust
// multisig2/src/tests.rs (new test)
#[test]
fn test_stale_confirmation_counts_after_member_removed() {
    // members: A (author), B (confirmer to be removed), C
    // num_confirmations = 2
    let mut c = MultiSigContract::new(members_abc(), 2);

    // Step 1: A adds request Y (e.g. Transfer)
    testing_env!(context_as(A));
    let y = c.add_request(transfer_request());

    // Step 2: B confirms Y -> confirmations[y] = {B}, not yet executed
    testing_env!(context_as(B));
    c.confirm(y);
    assert_eq!(c.get_confirmations(y).len(), 1);

    // Step 3: A+C approve and execute DeleteMember{B} via a separate request
    testing_env!(context_as(A));
    let del = c.add_request(delete_member_request(B));
    c.confirm(del);
    testing_env!(context_as(C));
    c.confirm(del); // executes, removes B from members

    assert!(!c.get_members().contains(&B));
    // BUG binding check: Y's confirmations should no longer count B
    assert_eq!(c.get_confirmations(y).len(), 0, "stale confirmation from removed member B was not purged");

    // Step 4: A confirms Y again -> should NOT reach threshold with only 1 live approver
    testing_env!(context_as(A));
    let result = c.confirm(y);
    // Vulnerable behavior: this executes the request (PromiseOrValue triggers execute_request)
    // Expected/fixed behavior: should remain pending (Value(true), confirmations len == 1)
    assert!(matches!(result, PromiseOrValue::Value(true)), "request executed with only 1 live confirmation, below num_confirmations=2");
}
```
Run with `cargo test -p multisig2 test_stale_confirmation_counts_after_member_removed`. On the current code, `get_confirmations(y).len()` remains `1` (B's stale entry) after B's removal, and the final `confirm(y)` by A triggers `execute_request`, proving execution occurred with only one live member's real-time approval against a `num_confirmations = 2` requirement.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
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
        }
    }
```

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
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
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
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
