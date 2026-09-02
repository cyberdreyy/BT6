### Title
Stale confirmations from removed multisig members are not purged, allowing execution of a request below the effective live-member threshold - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` treats the size of the `confirmations` set for a request as proof that `num_confirmations` distinct, currently-authorized members approved it. However, `delete_member` (and the equivalent `DeleteKey` handling in the legacy `multisig` contract) only purges requests *authored* by the removed member; it never scans other requests' `confirmations` sets to strip out approvals that removed member previously cast. A confirmation recorded by a member who is later removed from the multisig therefore continues to count toward the threshold, letting the request execute with fewer than `num_confirmations` *live* signers.

### Finding Description
`add_request`/`confirm` build up a `HashSet` of confirming members per `request_id`: [1](#0-0) 

The only invariant enforced at confirmation time is that the *caller* is currently a member (`assert_valid_request` → `current_member().is_some()`); it never re-validates the members already present in the stored `confirmations` set: [2](#0-1) 

When a member is removed via `DeleteMember`, cleanup only removes requests where that member was the original requester (`r.member == member`), and wipes those requests' confirmation sets entirely. It does not touch confirmations that removed member had *added* to other members' requests: [3](#0-2) 

The equality that should hold is:
```
confirmations(request_id) ⊆ current_members  AND  |confirmations(request_id)| >= num_confirmations
```
but the actual code only ensures the second half, since removed members' stale confirmations are never evicted from requests they didn't author. This breaks the "confirmations counted versus live members" binding: a request can reach the numeric threshold using a mix of live and no-longer-authorized signers.

The legacy `multisig/src/lib.rs` contract has the analogous gap in `DeleteKey`, which removes requests keyed by `signer_pk == pk` but does not strip that `pk`'s confirmations from other requests: [4](#0-3) 

### Impact Explanation
This is a Critical-impact issue per the rubric: "a multisig request executed below threshold." A coalition smaller than `num_confirmations` live members can get a request (e.g. a `Transfer` of the multisig's NEAR balance, a `FunctionCall`, or an `AddKey`/`AddMember` granting further control) executed by reusing a stale confirmation from a member who has since been removed, effectively moving NEAR or granting access without the required number of currently-authorized approvals.

### Likelihood Explanation
Requires a normal, expected sequence of multisig operations, no privileged/foundation access, no redeploy, and no compromised keys:
1. A member confirms a pending request (their own or another member's request they did not author).
2. The multisig later removes that member through the standard `DeleteMember`/`DeleteKey` governance flow (e.g., because that key/account is deemed compromised or is rotated out) — a routine, expected action.
3. The stale confirmation persists on any request that member is not the original requester of.
4. Remaining members can reach `num_confirmations` using that stale confirmation plus fewer live confirmations than intended.

This requires coordination among still-valid members (analogous to the "majority" attacker in the reference report) but no external victim key, no owner privilege beyond normal multisig membership, and is easily reproducible.

### Recommendation
When removing a member (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), iterate over **all** pending `requests`/`confirmations` (not just those authored by the removed member) and remove the removed member's entry from every confirmation set. Alternatively, validate at `confirm`-time and at threshold-check time that every entry in the `confirmations` set still corresponds to a current member of `self.members`, discarding stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` with a `Transfer` request `R` to attacker-controlled account → `confirmations(R) = {A}`.
3. `B` calls `confirm(R)` → `confirmations(R) = {A, B}` (below threshold, not yet executed): [1](#0-0) 
4. Members execute a separate, legitimately-passed `DeleteMember { member: B }` request (e.g. rotating out `B`'s key). `delete_member` only removes requests where `B` was the requester; `R` (authored by `A`) is untouched, and `B`'s confirmation stays inside `confirmations(R)`: [5](#0-4) 
5. `C` (now one of only 3 live members: A, C, D) calls `confirm(R)` → `confirmations(R).len() + 1 == 3 >= num_confirmations`, so `R` executes and transfers NEAR — even though only 2 currently-valid members (`A`, `C`) actually approved it, one short of the configured 3-of-4 threshold.

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
