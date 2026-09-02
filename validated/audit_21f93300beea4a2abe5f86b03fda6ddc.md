### Title
Stale confirmations from deleted multisig members are still counted toward the approval threshold, allowing requests to execute below the live-member confirmation quorum - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm()` decides whether to execute a request purely by comparing the size of the `confirmations` `HashSet` for that request against `self.num_confirmations`. When a member/key is removed via `DeleteMember`/`DeleteKey`, the contract only cleans up **requests originated by that member** and its `num_requests_pk` counter; it never scans the `confirmations` map to strip that member's prior *confirmation* entries from other still-pending requests. A confirmation cast by a member who is later removed therefore keeps counting toward quorum forever, letting a request execute with fewer live, currently-authorized confirmers than `num_confirmations` requires.

### Finding Description
`confirm()` in `multisig2/src/lib.rs` only checks set size vs `num_confirmations`: [1](#0-0) 

Membership removal (`delete_member`) only purges requests *created* by the removed member and the per-member request counter — it does not touch the `confirmations` `LookupMap<RequestId, HashSet<String>>` entries belonging to requests created by *other* members that the removed member had already confirmed: [2](#0-1) 

The equivalent legacy contract has the same gap for `DeleteKey`, which only removes requests keyed by the deleted public key, not that key's confirmations on other requests: [3](#0-2) 

The binding that should hold is:
`confirmations.len() == number of currently-live members who confirmed this request`

After a member is deleted while having an outstanding confirmation on another pending request, the actual invariant becomes:
`confirmations.len() == (live members who confirmed) + (removed members whose stale confirmation was never purged)`

so a request can reach `num_confirmations` with fewer than `num_confirmations` *currently authorized* signers, breaking the threshold-authorization boundary described in the rules ("confirmations counted versus live members").

### Impact Explanation
This is a Critical-class issue per the impact taxonomy: "a multisig request executed below threshold." A malicious or compromised member can pre-confirm a harmful request (e.g. `Transfer`, `AddKey`, `FunctionCall` draining funds) before being removed from the multisig (for unrelated reasons, by rotation, or even self-triggered via `DeleteMember`), and that stale confirmation remains valid forever. The remaining honest members, believing `num_confirmations` legitimate signers approved, unknowingly execute a request with one fewer live authorizer than the declared threshold — moving NEAR (or invoking privileged actions like `AddKey`/`AddMember`) below the intended quorum.

### Likelihood Explanation
Any multisig that ever exercises `DeleteMember` (or `DeleteKey` in the legacy contract) — a normal, expected operational action for member rotation — while a request is pending is affected; no special privilege beyond being a current or soon-to-be-removed member is required. Because member rotation is a routine multisig operation and requests can sit unconfirmed for extended periods, the precondition (a pending request with a stale confirmation from a member removed afterward) is easily reached, making this practically exploitable rather than purely theoretical.

### Recommendation
When removing a member/key (`delete_member` in `multisig2/src/lib.rs`, the `DeleteKey` branch in `multisig/src/lib.rs`), iterate over all entries in `confirmations` (not just requests originated by that member) and remove the deleted member's/key's entry from every `HashSet`. Alternatively, revalidate at execution time in `confirm()` that every account/key present in the request's confirmation set is still a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `D` (attacker or compromised member) calls `add_request_and_confirm` with a malicious `Transfer` request to itself — this creates the request and adds `D`'s confirmation (1/3).
3. Members legitimately submit and confirm `DeleteMember { member: D }` (e.g., because D's key was reported lost) — executed via `execute_request` → `delete_member`, per [2](#0-1) . This removes `D` from `members` but leaves `D`'s confirmation string in `confirmations` for the pending Transfer request from step 2.
4. `B` and `C` (2 live, legitimate members) later confirm the still-pending malicious Transfer request. In `confirm()`, `confirmations.len() as u32 + 1 >= self.num_confirmations` becomes `2 (stale D + B) + 1 (C) = 3 >= 3`, so `execute_request` fires and the transfer executes — even though only 2 currently-live members (`B`, `C`) ever approved it, one confirmation short of the declared 3-of-4 threshold.

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
