### Title
Stale confirmations from removed members counted toward threshold, allowing execution below live `num_confirmations` - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges pending requests/confirmations for requests that the removed member itself *created* (`r.member == member`), but never scans other members' confirmation sets for the deleted member's entry. `confirm()` then counts `confirmations.len()` without re-validating that every entry is still a live member, so a request can execute with a confirmation set that includes a since-removed member, breaking the invariant that an executed request is approved by `num_confirmations` members who are members at execution time.

### Finding Description
The binding that must hold is: for every executed request, `|{c ∈ confirmations(request_id) : c ∈ members}| ≥ num_confirmations` at the moment of execution. The code breaks this.

`delete_member` at [1](#0-0)  removes pending requests and their confirmation sets only for requests whose *original requester* (`request_with_signer.member`) equals the member being deleted:
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It never inspects `self.confirmations` entries where the deleted member is merely one of the *confirmers* of a request created by someone else. Those stale `member.to_string()` entries remain in the `HashSet<String>` for that request.

`confirm()` at [2](#0-1)  then just does `confirmations.len() as u32 + 1 >= self.num_confirmations` and executes the request — it never re-checks that every string in `confirmations` still corresponds to `self.members.contains(...)`. `assert_valid_request` at [3](#0-2)  also only validates the *caller*, not the historical confirmers.

Exploit flow (all reachable by ordinary multisig members, no privileged role beyond normal member status):
1. Member A creates request R1 (e.g. a `Transfer`).
2. Member B confirms R1 → `confirmations[R1] = {B}` (count 1, below threshold).
3. Members create/confirm a separate `DeleteMember { member: B }` request and execute it via `delete_member`. Since R1's creator is A (not B), R1's `requests`/`confirmations` entries are untouched — `confirmations[R1]` still contains `B`. B is now removed from `self.members`.
4. Remaining live members C and D each call `confirm(R1)`. `confirmations[R1]` grows to `{B, C, D}`, length 3, meeting e.g. `num_confirmations = 3`. `execute_request` runs and moves funds — even though only 2 currently-live members (C, D) actually approved it.

The `request_nonce` overflow detail mentioned in the question is not required for this exploit; the stale-confirmation defect is independent of the nonce value and reproducible with a small number of `add_request`/`confirm`/`delete_member` calls.

No existing guard (`assert_valid_request`, `assert_self_request`, `current_member`) checks staleness of previously recorded confirmations against the current `members` set.

### Impact Explanation
A multisig request (including a `Transfer` of NEAR, an `AddKey`, `AddMember`, or arbitrary `FunctionCall`) can be executed with fewer live-member approvals than `num_confirmations` requires, because a removed member's earlier confirmation is still counted. This directly matches the listed Critical impact: "a multisig request executed below `num_confirmations` live members," and can be used to move funds out of the multisig account or otherwise mutate contract/account state (add attacker-controlled keys, change confirmation thresholds) with insufficient live authorization. The defect is repeatable for every request that accumulated a confirmation before its confirmer was removed.

### Likelihood Explanation
This requires only normal multisig operations that any current member can trigger — no external unprivileged party needs privileged access, since the sequence (confirm a pending request, then later remove that confirmer, then have remaining members push the request over the threshold) can occur organically or be engineered by a member scheduled for removal. No special balances or account setups are needed beyond a standard multisig with ≥2 members and a request that hasn't reached quorum before a `DeleteMember` action executes. This is a straightforward, low-cost, deterministic sequence with `cargo test` reproducibility using `testing_env!`.

### Recommendation
When executing `DeleteMember`, iterate all entries of `self.confirmations` (not just requests created by the removed member) and strip the removed member's string from every confirmation `HashSet`. Alternatively, in `confirm()`, before counting confirmations, filter out any confirmer strings that no longer correspond to `self.members.contains(...)`, or persist confirmations keyed by member identity and re-validate membership at execution time rather than trusting the stored count.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_delete_member_meets_threshold() {
    // Setup: members = [A(account), B(account), C(access key), D(access key)], num_confirmations = 3
    // 1. As A: add_request(R1 = Transfer{...})
    // 2. As B: confirm(R1) -> confirmations[R1].len() == 1 (asserted)
    // 3. As (quorum not including B): add_request_and_confirm(DeleteMember{member: B})
    //    executed via 3 confirmations from A, C, D (none is B) -> members no longer contains B
    //    assert!(!c.get_members().contains(&B member))
    // 4. As C: confirm(R1) -> confirmations[R1].len() == 2 (contains stale "B" + "C")
    // 5. As D: confirm(R1) -> len reaches 3 >= num_confirmations -> execute_request runs
    //    BINDING CHECK (fails): live confirmers for R1 at execution = {C, D} (len 2)
    //                           but num_confirmations required = 3
    //    assert_eq!(c.requests.len(), 0); // request executed
    //    // Demonstrates execution occurred with only 2 live-member confirmations
    //    // against a threshold of 3, because "B" (removed) was still counted.
}
```
This test would show `c.requests.get(&R1)` becomes `None` (i.e., R1 executed) at step 5 while only C and D — both still in `c.get_members()` — actually approved it live, violating the `num_confirmations`-live-members invariant.

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
