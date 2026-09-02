### Title
Multisig Executes Requests Using Stale Confirmations From Removed Members, Breaking Confirmations-vs-Live-Members Binding - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
`confirm()` in both the `multisig` and `multisig2` contracts counts confirmations toward the `num_confirmations` threshold without ever revalidating that each recorded confirmer is still a current member. When a member (or access key) is removed via `DeleteKey`/`DeleteMember`, the contract only purges requests *created by* that member — it never scans other pending requests' confirmation sets to strip that member's stale confirmation. A request that was partially confirmed by a member who is later removed can still be pushed over the threshold and executed, using a confirmation count that no longer reflects the live membership set.

### Finding Description
In `multisig/src/lib.rs`, the `DeleteKey` handler inside `execute_request` only removes requests whose `signer_pk` (creator) equals the deleted key: [1](#0-0) 
It never touches `self.confirmations` entries for requests created by *other* signers that the deleted key had merely confirmed. `confirm()` then blindly trusts `confirmations.len()`: [2](#0-1) 

The same pattern exists in `multisig2/src/lib.rs`: `delete_member` only removes requests created by the deleted member, and does not scrub that member's confirmations from other pending requests: [3](#0-2) 
`confirm()` again just compares `confirmations.len() as u32 + 1 >= self.num_confirmations`, without checking membership of the existing confirmers: [4](#0-3) 

The equality that should hold is: `confirmations counted toward threshold == confirmations from accounts that are currently members`. Because stale confirmations are never invalidated when a member is removed, this equality can be violated: a request can execute with `num_confirmations` "votes" where one or more of those votes were cast by an account that is no longer authorized at execution time.

### Impact Explanation
This lets a `MultiSigRequestAction::Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request execute even though fewer than `num_confirmations` *currently live* members actually approved it. Concretely: with members `{A,B,C,D}` and `num_confirmations = 3`, if `B` and `D` confirm a Transfer request (2/3, pending), and `D` is subsequently removed through a separate, unrelated, properly-confirmed `DeleteMember`/`DeleteKey` request, `D`'s stale confirmation is never purged. A single additional confirmation from `C` then satisfies `confirmations.len() + 1 >= 3` and the Transfer executes — despite only `B` and `C` (2 of the 3 currently authorized members) ever having approved it. This is a multisig request executed below the intended live-member threshold, i.e. NEAR can move out of the multisig account with fewer real authorizations than the configured policy requires — a Critical-class custody binding violation ("a multisig request executed below threshold").

### Likelihood Explanation
This does not require any single member to act maliciously in the moment of exploitation — it is a byproduct of ordinary multisig lifecycle operations (partially confirming a request, then rotating/removing a member for routine reasons such as employee offboarding or key rotation) combined with the missing cleanup. Any organization that uses this multisig pattern with membership turnover and has outstanding unconfirmed requests is exposed; no compromise of a key or foundation-level privilege is needed beyond the normal governance actions the contract already permits.

### Recommendation
When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate all pending requests' confirmation sets (not just those created by the removed member/key) and remove the deleted member's entry from each; if this drops a request's confirmation count in a way that must be re-evaluated, leave it pending rather than allowing it to silently retain the stale vote. Alternatively, validate at `confirm()`/execution time that every counted confirmer is still a current member before allowing execution.

### Proof of Concept
1. Deploy `multisig2` (or `multisig`) with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` with a `Transfer` action to an external account (no auto-confirm).
3. `B` calls `confirm(request_id)` → confirmations = `{B}`.
4. `D` calls `confirm(request_id)` → confirmations = `{B, D}` (2/3, still pending).
5. Members separately create and fully confirm (by `A`, `B`, `C`) a `DeleteMember { member: D }` request — a normal, legitimate governance action removing `D` for unrelated reasons. `delete_member` ( [3](#0-2) ) removes `D` from `members` but does not touch the confirmation set of the Transfer request from step 2-4.
6. `C` calls `confirm(transfer_request_id)`. `confirmations.len() (2) + 1 >= num_confirmations (3)` is true, so `execute_request` runs the Transfer, even though only `B` and `C` — 2 of the 3 currently live members — ever approved it; `D`'s vote is stale and counted anyway.

### Citations

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

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

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
