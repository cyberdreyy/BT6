## Title
Stale Confirmations From Removed Multisig Members Still Count Toward `num_confirmations`, Allowing Execution Below Live-Member Threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation sets for requests that the *removed* member originally created — it never scrubs confirmation entries the removed member left on *other members'* still-pending requests. Because `confirm` counts confirmations purely by set size (`confirmations.len() as u32 + 1 >= self.num_confirmations`), a request can later be executed even though one of the counted "confirmations" belongs to an account that is no longer a multisig member, letting a request execute with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
When a member confirms a request, their identity is added to the `confirmations` set for that `request_id`: [1](#0-0) 

When a member is removed via the `DeleteMember` action, `delete_member` is invoked: [2](#0-1) 

This cleanup only removes requests where `r.member == member`, i.e. requests the removed member **created**. It does not iterate the `confirmations` map to strip the removed member's public key/account id from confirmation sets of requests **created by someone else** that the removed member had previously confirmed. Those stale confirmations remain in `self.confirmations` and continue to be counted by `confirm`'s threshold check: [3](#0-2) 

The binding that should hold is:
`live approving members ≥ num_confirmations` at execution time.

Instead, the actual check enforced is:
`|confirmations set (may include removed members)| ≥ num_confirmations`.

These diverge as soon as any confirming member is removed from `members` before the request reaches quorum and is later pushed over the threshold by a different, still-valid signer.

The identical pattern exists in the PK-based predecessor contract, where `DeleteKey` only removes requests signed (created) by that key, not confirmations it added to other requests: [4](#0-3) 

### Impact Explanation
This crosses the "confirmations counted versus live members" authorization boundary called out for this repo. A `MultiSigRequestAction::Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` request can be executed with only `num_confirmations - 1` (or fewer) live, currently-authorized members actually approving it, because one of the counted confirmations came from an account that has since been removed. This is a multisig request executed below its documented threshold, directly matching the Critical impact category (funds moved or privileged actions taken with insufficient live authorization).

### Likelihood Explanation
No special privileges are required by the party exploiting the stale state — any of the remaining live members (or a compromised/malicious one) can simply wait for a partially-confirmed request to have a confirming member removed (a routine key-rotation/security event, e.g. removing a compromised key via `DeleteMember`/`DeleteKey`), then supply the final confirmation themselves to push the request over threshold using the stale confirmation as "padding." This requires only ordinary multisig operations (pending request + member removal + final confirm) with no need for a redeploy or foundation action, making it practically reachable in normal operational flows (e.g., rotating out a suspected-compromised signer while a request is mid-flight).

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, and the analogous `DeleteKey` handling in `multisig/src/lib.rs`), iterate all entries in `self.confirmations` (not just requests created by that member) and remove the departing member's identity from every confirmation set, or re-validate at `confirm`-time / execution-time that every entry in a request's confirmation set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(request)` for a `Transfer` — confirmations = `{A}` (1/3).
3. `B` calls `confirm(request_id)` — confirmations = `{A, B}` (2/3), request not yet executed.
4. Members submit and confirm a separate `DeleteMember { member: B }` request (reaching its own 3-of-4 threshold) — `B` is removed from `self.members`, and via `delete_member` only requests *created by* `B` are purged; the pending `Transfer` request created by `A` is untouched and still has `confirmations = {A, B}`.
5. `C` (still a live member) calls `confirm(request_id)`: [5](#0-4) 
   `confirmations.len() as u32 + 1 == 3 >= num_confirmations (3)` → request executes.
6. Result: the `Transfer` executed with confirmations from only `A` and `C` as live members plus one stale confirmation from removed member `B` — i.e., 2 live approvals satisfied a 3-of-4 policy.

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
