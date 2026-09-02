### Title
Stale confirmations from removed multisig members are still counted toward the approval threshold, allowing a request to execute below the configured K-of-N threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` decides whether a request is ready to execute purely by comparing the *size* of the stored `confirmations: HashSet<String>` against `num_confirmations`. It never re-validates that every entry in that set still corresponds to a *current* member of `self.members`. `delete_member()` only purges confirmations for requests that were *originated* by the removed member; it does not scrub that member's confirmation entries from other pending requests that they merely confirmed. This is the exact analog of the CDPVault/PoolV3 bug: a "recorded" quantity (confirmations counted) silently diverges from the "real" quantity (live approving members), and the divergence benefits the party executing the stale-approved request.

### Finding Description
`confirm()` reads the request's confirmation set and executes as soon as `confirmations.len() + 1 >= self.num_confirmations`: [1](#0-0) 

Membership is stored as an `UnorderedSet<MultisigMember>` and is checked only when the *caller* confirms (via `current_member()`), not retroactively for confirmations already stored by previously-valid, now-removed members: [2](#0-1) 

`delete_member()` removes the departing member from `self.members` and purges pending requests/confirmations, but the purge is scoped only to requests whose `request_with_signer.member == member` (i.e., requests that member *created*): [3](#0-2) 

If the removed member had instead only *confirmed* (not created) some other pending request, that confirmation entry stays in `self.confirmations` for that `request_id` forever, because nothing else in the codebase ever revisits or invalidates confirmations by membership after the fact.

The binding that should hold is:
```
confirmations_counted(request) == |{ m ∈ live_members : m confirmed request }|
```
After a member is removed while having an outstanding confirmation on a request they did not create, the actual invariant becomes:
```
confirmations_counted(request) > |{ m ∈ live_members : m confirmed request }|
```
so a request can reach `num_confirmations` with fewer than `num_confirmations` currently-authorized approvers.

### Impact Explanation
This maps to the Critical impact category "a multisig request executed below threshold." A K-of-N multisig's entire security model rests on requiring K live, current signers to authorize any action (transfers, `AddKey`, `DeployContract`, etc.). With this bug, a request can execute having only `K-1` (or fewer) live approvals plus one or more stale approvals from members who have since been removed — silently weakening the multisig below its configured threshold and enabling execution of transfers or privileged actions (e.g. `AddKey`, `DeployContract`) that should have been blocked.

### Likelihood Explanation
This requires no exotic privilege beyond the normal, expected lifecycle of any long-lived multisig: member turnover (`DeleteMember`) is a routine governance action, not an attack in itself. The only necessary condition is the ordinary sequence: (1) a pending request exists that was created by member X but confirmed by member Y, (2) Y is later removed from the multisig via a separate, legitimate `DeleteMember` action, (3) the original request is subsequently confirmed by enough additional live members to reach the *stale* threshold count. This is easily triggered in practice any time membership changes while requests are outstanding, which is a normal operational pattern for these contracts (e.g. `active_requests_limit` of 12 and no forced request expiry beyond the 15-minute `REQUEST_COOLDOWN` for deletion).

### Recommendation
When a member is deleted, iterate over *all* requests' confirmation sets (not just those the member created) and remove that member's confirmation entry from each. Alternatively, at `confirm()` time, recompute the confirmation count by intersecting the stored confirmations with `self.members` before comparing against `num_confirmations`, so stale confirmations from removed members never count toward the threshold.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `B` calls `add_request_and_confirm(R)` where `R` is a malicious `Transfer` to an attacker-controlled account. `confirmations[R] = {B}`, and `R.member == B` (creator).
3. `A` calls `confirm(R)`. `confirmations[R] = {B, A}` (2/3, below threshold — execution does not fire yet): [4](#0-3) 
4. Separately, `B`, `C`, `D` execute a legitimate `DeleteMember { A }` request (routine offboarding). `delete_member()` only clears requests where `r.member == A`; since `R.member == B`, `R`'s confirmation set is left untouched, still containing stale entry `A`: [5](#0-4) 
5. Live members are now `{B, C, D}`. `C` calls `confirm(R)`. `confirmations[R] = {B, A, C}`, length 3, which is `>= num_confirmations (3)`, so `execute_request(R)` fires and the transfer executes: [6](#0-5) 
6. In reality only `B` and `C` are current, valid approvers (2 of 3 required); `A`'s stale confirmation from before removal was counted as if it were a live approval, so the request executed one confirmation short of the configured threshold — an unauthorized transfer below the K-of-N requirement.

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

**File:** multisig2/src/lib.rs (L322-339)
```rust
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
