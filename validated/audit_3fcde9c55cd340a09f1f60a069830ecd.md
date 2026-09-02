Same bug class exists in the original `multisig/src/lib.rs` `DeleteKey` handler: it only purges requests/confirmations keyed by requests *created* by the deleted public key (`r.signer_pk == pk`), not confirmations that key gave on *other* requests. So the vulnerability generalizes to both `multisig` and `multisig2`.

### Title
Stale confirmations from removed multisig members/keys are not purged, allowing a request to execute below the live-member confirmation threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` decides whether to execute a request purely by counting entries in the `confirmations` set for that `request_id` against `num_confirmations`. When a member/key is removed via `DeleteMember`/`DeleteKey`, the contract only deletes requests whose *creator* was that member/key; it never scans other pending requests' `confirmations` sets to strip out confirmations that member previously cast. A stale confirmation from a now-removed member therefore keeps counting toward quorum, letting a request execute with fewer *live* confirmations than `num_confirmations` actually requires.

### Finding Description
`multisig2/src/lib.rs::confirm` at [1](#0-0)  executes the request as soon as `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no re-validation that each entry in `confirmations` still corresponds to a current member of `self.members`.

`delete_member` at [2](#0-1)  only purges requests created by the removed member (`r.member == member`) and clears `num_requests_pk` for that member; it does not iterate over `self.confirmations` to remove that member's string from confirmation sets of requests they merely *confirmed* (but did not create). Once the member is deleted, their `MultisigMember::to_string()` entry remains embedded in any `HashSet<String>` for requests they had previously confirmed.

The equivalent issue exists in the older `multisig/src/lib.rs` contract: the `DeleteKey` action at [3](#0-2)  only removes requests where `r.signer_pk == pk` (i.e., requests created by that key), leaving stale confirmations by that key on other requests untouched.

This breaks the intended custody binding: `num_confirmations` is meant to equal the number of *live, currently-authorized* signers who approved a request before any privileged action (including `Transfer`, `AddKey`, `FunctionCall`, etc.) executes. After a member is removed, `live confirmations on request R < num_confirmations` can still evaluate as `stored confirmations on R >= num_confirmations`, because the deleted member's stale confirmation is indistinguishable from a live one.

### Impact Explanation
This is a multisig request executed below the effective threshold of currently authorized signers — a Critical-impact custody violation per the rules (funds moved / privileged action executed with fewer than `k` live approvals). An attacker or compromised member's authority (via account or access key) can be baked into a pending request's confirmation count even after being deleted from the multisig, allowing the remaining signers (fewer than `k`) to push through a `Transfer`, `AddKey`, `FunctionCall`, `DeployContract`, etc.

### Likelihood Explanation
This requires no privileged/attacker access beyond normal multisig usage: a member confirms a request, is later legitimately removed (e.g., off-boarding, key rotation, compromise response), and any remaining pending request they confirmed retains their stale approval. Since requests can remain pending for extended periods (only bounded by `active_requests_limit`/`REQUEST_COOLDOWN` for deletion, not automatic expiry tied to membership changes), this is readily reachable in normal contract operation without any owner/attacker collusion needed.

### Recommendation
When a member/key is removed (`delete_member` in `multisig2`, `DeleteKey` in `multisig`), iterate over **all** pending requests' confirmation sets (not just those created by the removed member) and strip the removed member's entry from each. Alternatively, validate at `confirm()` time that every entry in `confirmations` still belongs to `self.members` (recomputing the live count) before comparing against `num_confirmations`, rather than trusting the raw stored `len()`.

### Proof of Concept
1. Deploy `multisig2` with members `[M1, M2, M3, M4]` and `num_confirmations = 3`.
2. `M1` calls `add_request` creating request `R1` (e.g., `Transfer`).
3. `M2` calls `confirm(R1)` → `confirmations(R1) = {M2}` (1 of 3, not yet executed) — see [4](#0-3) .
4. `M1`, `M3`, `M4` create and confirm a separate `DeleteMember{M2}` request (self-request), which passes the `members.len() - 1 >= num_confirmations` check at [5](#0-4)  and executes, removing `M2` from `self.members` and its access key — but `confirmations(R1)` still contains `M2`'s string entry since `R1` was created by `M1`, not `M2` (`r.member == member` filter at [6](#0-5)  does not match).
5. `M3` calls `confirm(R1)` → `confirmations(R1) = {M2, M3}` (2 of 3).
6. `M4` calls `confirm(R1)` → `len()+1 = 3 >= num_confirmations(3)` → `R1` executes the `Transfer`, even though only `M3` and `M4` are still live members who approved it (2 live approvals versus the required 3). [1](#0-0) [2](#0-1)

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

**File:** multisig/src/lib.rs (L198-215)
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
```
