### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
The Axelar report describes past operator sets remaining valid signers after `transferOperatorship`, letting stale signatures satisfy the *current* threshold. The exact same class of bug exists in this repo's multisig contracts: when a member is removed via `DeleteMember` (or a key via `DeleteKey` in the legacy `multisig` contract), the contract only purges pending *requests originated by* that member — it never scrubs that member's already-recorded *confirmations* on other pending requests. Those stale confirmations continue to count toward `num_confirmations`, so a request can later execute with fewer live, currently-authorized approvers than the configured threshold requires.

### Finding Description
`confirm()` in `multisig2/src/lib.rs` decides execution purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set: [1](#0-0) 

Membership removal is handled by `delete_member`, which only removes pending requests that were *created by* the departing member, then removes them from `self.members`: [2](#0-1) 

It does not iterate `self.confirmations` to strip the removed member's identity from confirmation sets of *other* pending requests they had already confirmed. Consequently, `confirmations.len()` for those other requests still includes the removed member, even though `self.members` (the current, "live" set) no longer contains them.

The binding that should hold is:
`confirmations counted for request R == confirmations from members ∈ current live members set`

After a `DeleteMember` action, this equality is broken: a stale confirmation from a now-removed member is still counted, exactly analogous to the Axelar bug where signatures from past operator epochs were still counted after `transferOperatorship`.

The same root cause exists in the legacy `multisig/src/lib.rs` contract's `DeleteKey` action, which only clears requests whose original `signer_pk` equals the deleted key, not confirmations recorded on other requests: [3](#0-2) 

### Impact Explanation
This allows a multisig request to be executed with fewer live/current confirmations than `num_confirmations` requires — i.e., "a multisig request executed below threshold," which is explicitly a Critical-impact category. Funds transfers, key/member changes, or arbitrary `FunctionCall` actions gated by the multisig can be pushed through with an effectively lower live threshold than configured, undermining the k-of-n security guarantee the contract is meant to enforce.

### Likelihood Explanation
No privileged actor, redeploy, or external validator misbehavior is required. Any legitimate member sequence of ordinary contract calls — add a request, get it partially confirmed, then have the multisig itself remove one of the confirming members via a separate `DeleteMember` request, then have a different current member supply the final confirmation — reproduces this. This is a normal, reachable operational sequence (member rotation is an expected multisig lifecycle event), not a contrived edge case.

### Recommendation
When executing `DeleteMember` (and `DeleteKey` in the legacy contract), iterate all pending requests' confirmation sets and remove the departing member's/key's entry wherever present, not just requests they originated. Alternatively, validate confirmations against `self.members` at `confirm()` time (i.e., recompute the confirmation count using only entries that are still current members) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{M1, M2, M3, M4}` and `num_confirmations = 3`.
2. `M1` calls `add_request` then `confirm` (via `add_request_and_confirm`) on request `R` (e.g. a `Transfer` action) → `confirmations[R] = {M1}`.
3. `M2` calls `confirm(R)` → `confirmations[R] = {M1, M2}` (2 of 3 needed).
4. Members submit and confirm a separate request that performs `DeleteMember { member: M2 }`. `delete_member` (`multisig2/src/lib.rs:356-379`) removes `M2` from `self.members` and deletes only requests where `r.member == M2` — request `R` (created by `M1`) is untouched, so `confirmations[R]` still contains `M2`.
5. `M3` (a live member) calls `confirm(R)`. `confirmations.len() + 1 == 3 >= num_confirmations (3)` at `multisig2/src/lib.rs:304`, so `R` executes — even though only `M1` and `M3` are actually live, currently-authorized approvers; `M2`'s stale confirmation was counted to reach the threshold. [4](#0-3)

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
