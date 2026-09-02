### Title
Confirmations from removed multisig members are not purged, allowing execution below the effective live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only removes pending *requests* that were created by the deleted member, but never scrubs that member's *confirmations* recorded on requests created by other members. Because `confirm()` counts entries in the `confirmations` set without checking that each entry still belongs to a current member, a request can be executed using one or more confirmations from an account/key that has already been deleted from the multisig, effectively executing a transaction with fewer than `num_confirmations` live approvals.

### Finding Description
`confirm()` treats the size of the stored `HashSet<String>` for a request as the count of valid approvals: [1](#0-0) 

It never re-validates that every account/public-key string already present in `confirmations` is still a current member — it only validates the *caller* via `assert_valid_request` → `current_member()`.

`delete_member` is the only path that prunes state when a member is removed, and it only deletes requests whose *creator* (`r.member`) is the removed member; it does not touch `confirmations` entries the removed member made on other members' requests: [2](#0-1) 

Concrete break of the "confirmations counted versus live members" binding:
- Multisig has members {A, B, C, D, E}, `num_confirmations = 3`.
- D creates a request (`add_request`); A and B confirm it (2 confirmations, one short of 3).
- A self-removed request completes: members vote to `DeleteMember { A }` via `execute_request` → `delete_member`. This only removes requests created by A; it leaves A's confirmation on D's still-pending request untouched.
- Now only {B, C, D, E} are live members and threshold is still 3.
- C confirms D's request: `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates `2 + 1 >= 3` and the request executes.
- But only B and C are still live members who explicitly approved it (A's approval is now from a non-member). The request executed with 2 live confirmations against a `num_confirmations` threshold of 3.

This exactly matches the analog: `confirmations counted != live members who actually approved`, breaking the K-of-N custody guarantee documented for the contract (`README.md`: "Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved").

### Impact Explanation
This allows a `MultiSigRequest` — including `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeployContract`, etc. — to execute despite not having `num_confirmations` worth of currently-authorized approvers. In the funds-moving case (`Transfer`), NEAR can leave the multisig account with fewer live signer approvals than the K-of-N policy requires, i.e. "a multisig request executed below threshold," which is explicitly listed as a Critical impact.

### Likelihood Explanation
No privileged role or victim key is required beyond the normal multisig operation flow (members creating/confirming requests and later removing a member) — all of these are ordinary, in-scope multisig actions. The bug is deterministic: any member removal that occurs while other requests have outstanding confirmations from the removed member leaves those stale confirmations counted forever (or until the request is separately deleted/executed). Given that removing members (key rotation, offboarding) is a normal and expected multisig lifecycle event, and pending requests routinely persist across such changes (there is no time bound tying request lifetime to membership changes), this is readily triggerable by the multisig's own members without any additional compromise.

### Recommendation
When a member is deleted, iterate over `self.confirmations` for every outstanding request and remove the deleted member's confirmation entry as well, not only requests they authored. Alternatively, filter `confirmations.len()` in `confirm()` to only count entries in `self.members` at the time of the check (or lazily prune stale entries lazily during `confirm`), so the executed count always reflects currently live members.

### Proof of Concept
Given the existing test harness structure (`multisig2/src/lib.rs` tests, e.g. `test_change_num_confirmations`, `test_multi_3_of_n`):

1. `MultiSigContract::new(members = [A,B,C,D,E], num_confirmations = 3)`.
2. As D: `add_request(request)` targeting a `Transfer`.
3. As A: `confirm(request_id)` → confirmations = {A}.
4. As B: `confirm(request_id)` → confirmations = {A,B}.
5. Execute a separate request (confirmed by 3 members) with `DeleteMember { member: A }` action → `delete_member` runs; it does not touch `confirmations` of D's still-pending request, so `confirmations = {A,B}` remains, and A is no longer in `self.members`.
6. As C: `confirm(request_id)` → `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `execute_request` runs the `Transfer`.
7. Result: the transfer executed with confirmations from {A(removed), B, C} — only 2 of the 3 confirmers (B, C) are current members, i.e., the request executed with fewer than `num_confirmations` live approvals. [3](#0-2) [4](#0-3)

### Citations

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
