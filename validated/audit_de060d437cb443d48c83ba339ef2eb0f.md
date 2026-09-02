## Analysis

The reported Paladin bug is a **stale-cached-value** pattern: a value computed at one point in time (`votesDifference`) is reused later without being refreshed against the current live state, so the on-chain accounting diverges from reality. Scanning the in-scope NEAR contracts for the same class of bug — a binding between *recorded/counted state* and *live state* that silently breaks — turns up a directly analogous defect in `multisig2` (and the older `multisig`): **confirmations are counted by a raw set-length check, but a removed member's stale confirmation is never purged from requests they didn't originate**, so `confirm()` can execute a request whose confirmation count is inflated by non-members.

### Title
Stale confirmations from removed members still count toward `num_confirmations` threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only strips a departing member's *self-originated* requests and their `num_requests_pk` entry; it never scans and removes that member's confirmation entries on requests that *other* members originated. `confirm()` then authorizes execution purely by comparing `confirmations.len()` to `self.num_confirmations`, without verifying every recorded confirmer is still `self.members.contains(...)`. This breaks the intended equality `confirmations_counted(request) == live_member_confirmations(request)`.

### Finding Description
`MultiSigRequestAction::DeleteMember` is handled by `delete_member`: [1](#0-0) 

It filters `self.requests` for entries where `r.member == member` (i.e., requests *submitted* by the departing member) and deletes those requests plus their confirmation sets. It does **not** iterate `self.confirmations` to strip the departing member's `to_string()` entry from confirmation sets of requests submitted by *other* members.

`confirm()` executes a request purely based on the cardinality of the confirmation set: [2](#0-1) 

There is no re-validation at execution time that every string in `confirmations` still corresponds to a current `self.members` entry.

Consequence: if member A confirms a request R that was created by member B, and A is later removed via `DeleteMember`, R keeps A's confirmation. Any subsequent live member's confirmation can push `confirmations.len() + 1 >= num_confirmations` and execute R — meaning the request runs with fewer *live* approvals than the configured K-of-N threshold requires.

The identical pattern exists in the older `multisig` contract's `DeleteKey` handling, which also only purges requests originated by the removed key and its `num_requests_pk` counter, not confirmations left on other members' requests: [3](#0-2) 

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary explicitly called out as in-scope. A K-of-N multisig's entire security guarantee is that a request only executes once K *currently trusted* members approve it. With this bug, a request (e.g. `Transfer`, `AddKey`, `FunctionCall`) can execute with confirmations from fewer than K live members — effectively a multisig request executed below the intended threshold, matching the Critical impact criterion ("a multisig request executed below threshold"). Since multisig requests directly control NEAR transfers and key/account management, this can lead to unauthorized movement of funds held by the multisig account.

### Likelihood Explanation
No privileged actor, redeploy, or social engineering is required beyond the multisig's own normal, documented operation: members confirming requests and later removing a member (e.g., because that member's key was compromised or they left the organization — the exact scenario `DeleteMember`/`DeleteKey` exists to handle). The only "attacker" precondition is that a request was confirmed by a member before that member was removed, which is a routine sequence, not a contrived edge case. Any remaining member's ordinary confirmation on an already-partially-confirmed request can trigger execution using the stale confirmation, without themselves realizing they are enabling threshold bypass.

### Recommendation
When removing a member (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), iterate all pending requests' confirmation sets (not just those the member originated) and strip the departing member's identity from each. Alternatively/additionally, `confirm()` should recompute the *live* confirmation count by filtering the stored confirmation set against `self.members.contains(...)` before comparing to `num_confirmations`, so stale entries can never contribute to the threshold.

### Proof of Concept
1. Deploy `multisig2` with 4 members `{M1, M2, M3, M4}` and `num_confirmations = 3`.
2. `M2` calls `add_request` with a `Transfer` action to an attacker-controlled account (request `R`).
3. `M1` calls `confirm(R)` → `confirmations[R] = {M1}` (1/3).
4. `M3` calls `confirm(R)` → `confirmations[R] = {M1, M3}` (2/3, not yet executable).
5. The multisig legitimately removes `M1` (e.g. key compromise) via a separate `DeleteMember{member: M1}` request confirmed by `M2, M3, M4`. `delete_member` only deletes requests where `r.member == M1` (M1's *own* submissions) — `R` was submitted by `M2`, so `R` and its confirmation set `{M1, M3}` survive untouched, per `multisig2/src/lib.rs` lines 361-371 above.
6. `M4` calls `confirm(R)` → `confirmations.len() (2) + 1 >= num_confirmations (3)` is true, so `R` executes the `Transfer`, even though only `M3` and `M4` are live confirming members — one short of the required 3 live confirmations. The threshold has effectively been bypassed via `M1`'s stale, post-removal confirmation.

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
