### Title
Multisig executes below its confirmation threshold using stale confirmations from deleted members - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
`MultiSigContract::confirm` in both `multisig2/src/lib.rs` and `multisig/src/lib.rs` counts the size of the stored `confirmations` set for a request against `num_confirmations` without verifying that every entry in that set still corresponds to a currently active member. `delete_member` (multisig2) / the `DeleteKey` action (multisig1) only purges requests *created* by the removed member/key; confirmations that removed member had already cast on *other, still-pending* requests are left untouched in the `confirmations` map. This lets a request execute with fewer live, currently-authorized confirmations than `num_confirmations` actually requires.

### Finding Description
The multisig state machine is supposed to guarantee: a request executes only once at least `num_confirmations` **current** members have approved it. The invariant that should hold is:

`count(confirmations ∩ current_members) >= num_confirmations`

But the code only checks:

`confirmations.len() + 1 >= num_confirmations` [1](#0-0) 

`confirmations` is a `HashSet<String>` keyed by `member.to_string()`, populated whenever `confirm()` is called [2](#0-1) . When a member is later removed via `delete_member`, the cleanup logic only removes **requests whose creator (`r.member`) equals the deleted member**, and clears `num_requests_pk` for that member — it does not scan other pending requests' `confirmations` sets to strip out that member's prior confirmations: [3](#0-2) 

The same pattern exists in the legacy `multisig/src/lib.rs` for the `DeleteKey` action, which filters `request_ids` by `r.signer_pk == pk` (creator only) before wiping confirmations, leaving confirmations that key made on *other* requests intact: [4](#0-3) 

`current_member()` re-validates the *caller* of `confirm()` against the live `members` set [5](#0-4) , but it never re-validates the members whose confirmations are *already stored* in the set from earlier calls. `assert_valid_request` similarly only checks that the request/confirmations records exist, not that each recorded confirmer is still a member [6](#0-5) .

Root cause: the state transition performed by "delete member" is too lenient with respect to already-recorded confirmations on unrelated, still-pending requests — a stale approval from a party that has since had its authority revoked continues to count toward the live-member confirmation threshold, exactly analogous to the DCS report's pattern of a loosely-checked state transition reviving a status that should have been invalidated.

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary called out in scope. A pending high-value request (e.g., a `Transfer` of the multisig account's NEAR balance) can be pushed to execution with strictly fewer currently-authorized approvals than `num_confirmations` mandates, because a stale confirmation from an already-removed member is counted. This is a **multisig request executed below threshold** — a Critical-impact outcome per the stated impact categories, since it allows funds/actions to be authorized without the required live quorum.

### Likelihood Explanation
The scenario requires only ordinary multisig operations that members are expected to perform routinely: (1) a member confirms a pending request without reaching threshold, (2) at some later point (for legitimate reasons, e.g., key rotation/offboarding) a member is removed via a normal `DeleteMember`/`DeleteKey` request, and (3) the remaining request is later confirmed by another still-active member. No foundation privilege, victim key, redeploy, or off-chain assumption is needed — it is a natural sequence any operator of a k-of-n multisig could trigger, deliberately or accidentally. Given that member turnover (revoking a compromised/departing signer) is one of the primary reasons `DeleteMember`/`DeleteKey` exists, this ordering is realistic.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` handling in `multisig/src/lib.rs`), iterate over **all** pending requests' confirmation sets (not just those the removed member created) and strip any confirmation entry belonging to the removed member/key. Alternatively, when counting confirmations in `confirm()`, filter the stored confirmations set against the current `members` set before comparing against `num_confirmations`, so stale entries from removed members never count toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C], num_confirmations = 2)`.
2. Member `A` calls `add_request` to create request `R` (e.g., `Transfer` of the account's balance to an attacker-controlled account).
3. Member `B` calls `confirm(R)`. `confirmations.len() (0) + 1 = 1 < 2`, so `B` is only added to the confirmations set for `R`: `confirmations[R] = {B}`.
4. Later, through a separate, otherwise-legitimate request, the members remove `B` via `DeleteMember`/`DeleteKey` (permitted since `members.len() - 1 (2) >= num_confirmations (2)`). `delete_member`/`DeleteKey` cleanup only removes requests *created* by `B`; since `R` was created by `A`, `R` and `confirmations[R] = {B}` are left untouched. `B` is now fully removed from `members`.
5. Member `A` calls `confirm(R)`. `current_member()` validates `A` is still a member (true). `confirmations.len() (1, from stale B) + 1 = 2 >= num_confirmations (2)` — the request executes, transferring funds, even though only one currently live member (`A`) actually approved it and `B`'s stale, no-longer-authorized approval was used to reach the "2-of-3" threshold.

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
