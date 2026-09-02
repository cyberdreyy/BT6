### Title
Confirmations From Deleted Multisig Members/Keys Still Count Toward Execution Threshold, Allowing a Request to Execute Below the Live-Member Threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` implement `DeleteKey`/`DeleteMember` cleanup that only purges pending *requests originated by* the removed key/member, and the `confirmations` set attached to *those* requests. It never scans other pending requests to strip out a confirmation that the removed key/member had previously cast as a *confirmer*. Because `confirm()` counts raw entries in the `confirmations` set against the live `num_confirmations` threshold without validating that each entry still corresponds to a current member, a stale confirmation from an already-deleted member is still counted, letting a request execute with fewer *live* confirmations than the configured threshold.

### Finding Description
`confirm()` in `multisig/src/lib.rs` (and the equivalent in `multisig2/src/lib.rs`) works purely off the `confirmations` set size versus the global `num_confirmations`: [1](#0-0) 

When a `DeleteKey` action executes, the code removes requests *created by* the deleted key and their confirmation sets, and removes the `num_requests_pk` entry for that key — but it does not touch confirmation sets belonging to *other* pending requests where the deleted key had already cast a confirmation: [2](#0-1) 

The multisig2 variant has the identical gap in `delete_member`, which similarly only removes requests where `r.member == member` and their confirmations, leaving stale confirmations from that member on unrelated requests untouched: [3](#0-2) 

This breaks the custody/authorization binding the README documents: "Any of the access keys can confirm, until the required number of confirmation achieved," which implicitly assumes confirmations are given by *currently valid* keys/members. Once a member is removed, their prior confirmation should no longer be able to help meet the `num_confirmations` threshold, but the code has no mechanism to invalidate it.

### Impact Explanation
This is a Critical-class issue per the custody binding: "a multisig request executed below threshold." A request that was pending with confirmations from members who have since been removed can be pushed over the execution threshold with fewer *currently authorized* signers than intended, because the removed member's stale confirmation still counts. Since `execute_request` for actions like `Transfer`, `AddKey`, `FunctionCall`, `DeployContract`, etc. moves funds or grants access directly from the multisig account, this can result in funds or access being moved/granted without the number of live approvals the contract's `K`-of-`N` security model requires.

### Likelihood Explanation
This requires an ordinary sequence of otherwise-legitimate multisig operations, no attacker-controlled deployment, no privileged bypass, and no exotic reentrancy: (1) a member is later removed (revoked key/compromised key handling, employee offboarding, key rotation), while (2) a request they had already confirmed remains pending in the `requests`/`confirmations` maps. This is a normal, expected pattern for any long-lived multisig with membership churn — pending requests are not required to be flushed before removing a member, and the contract provides no way to audit/clean stale confirmations. Any remaining live member who calls `confirm()` on that stale request will unknowingly execute it under a deflated effective threshold.

### Recommendation
When executing `DeleteKey`/`DeleteMember`, iterate over all entries in `self.confirmations` (not just those for requests signed by the removed key/member) and strip out the removed key/member's confirmation from every set. Alternatively, validate at `confirm()`-time (or at counting-time) that each recorded confirmer is still present in the current member/key set, discounting confirmations from members who are no longer part of the multisig before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy a multisig with `num_confirmations = 3` and members `A, B, C, D`.
2. `A` calls `add_request` to create request `R` (e.g., a `Transfer` of the full balance to an attacker-controlled account), then `A` calls `confirm(R)` → `confirmations = {A}` (1 < 3, stored, not executed).
3. `B` calls `confirm(R)` → `confirmations = {A, B}` (2 < 3, stored, not executed).
4. Separately, through a legitimate/independent governance action reaching the required threshold, member `B` is removed from the multisig via a `DeleteKey`/`DeleteMember` request (e.g., because `B`'s key was compromised or rotated). Per `execute_request`'s `DeleteKey`/`DeleteMember` branch, only requests *created by* `B` are purged; `R` (created by `A`, confirmed by `B`) is untouched, and `B`'s confirmation entry in `confirmations` for `R` is never removed.
5. `C` (a remaining live member, unaware `B`'s confirmation is stale) calls `confirm(R)`. The check `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates `2 + 1 >= 3` → true, and `R` executes.
6. `R` (the `Transfer`) executes with only two currently-valid confirmations (`A`, `C`) plus one from a now-removed member (`B`), i.e., below the intended live-member threshold of 3, moving funds out of the multisig account with insufficient live authorization.

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
