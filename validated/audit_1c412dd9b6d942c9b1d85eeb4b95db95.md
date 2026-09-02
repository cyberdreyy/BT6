Analysis of the multisig/multisig2 contracts confirms a genuine analog to the "authorization recorded against an identity that no longer holds that authority" class of bug from the external report. In the auction case, a highest-bid amount was recorded against the assumption that the bidder was also the current owner; that assumption silently broke when the NFT's ownership changed mid-flight via a code path (`mintToken`) that the accounting logic never re-checked. The same pattern — a stale authorization credited to an identity whose membership status has since changed — exists in `confirm()` in `multisig/src/lib.rs` and `multisig2/src/lib.rs`.

### Title
Confirmations from a deleted multisig member remain valid and can be counted toward executing a request, allowing a removed member to still authorize fund transfers - (File: `multisig2/src/lib.rs`)

### Summary
`confirm()` in `multisig2/src/lib.rs` counts a `HashSet<String>` of confirmer identities against `self.num_confirmations` without ever verifying that every previously recorded confirmer is still a current member of `self.members` at execution time. `delete_member()` only purges requests and confirmations *authored* by the removed member, not confirmations that member previously *cast* on other members' requests.

### Finding Description
`delete_member()` at [1](#0-0)  removes outstanding requests filtered by `r.member == member` (i.e., requests the deleted member had *originated*), and removes `num_requests_pk` for that member, but it never scans `self.confirmations` (a `LookupMap<RequestId, HashSet<String>>`) to strip out confirmations that the removed member cast on requests *originated by other members*.

`confirm()` at [2](#0-1)  only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` — it re-validates that the *calling* member is currently a member via `current_member()` and `assert_valid_request()`, but it never re-validates that the members whose confirmations are *already stored* in the set are still current members.

Concretely: if member A adds a request (`add_request`) and member B confirms it (`confirm`, adding `B` to the `confirmations` set), and then a separate `DeleteMember { member: B }` request executes, `delete_member` will not touch the original request or its confirmation set, since that request was authored by A, not B. B's confirmation for A's still-pending request remains counted. If `num_confirmations` was e.g. 2 out of {A, B, C}, and B is later replaced (e.g. B's key was compromised and the remaining members remove B specifically for that reason), A's stale request now only needs 1 more live confirmer to reach the old threshold, effectively still counting the removed/untrusted B's vote as one of the K required signatures — the same “confirmations counted versus live members” custody binding called out in the task's list of relevant equalities.

The multisig1 (`multisig/src/lib.rs`) version has the analogous gap in `DeleteKey` handling at [3](#0-2) , which likewise only purges requests where `r.signer_pk == pk`, leaving confirmations cast by that key on other keys' requests intact.

### Impact Explanation
This breaks the equality that should hold: *confirmations credited toward the K-of-N threshold == confirmations from members who are currently part of N*. Once a member is removed (for any reason — compromised key, revoked trust, employee offboarding), their previously cast confirmations on other still-open requests continue to count. Since `execute_request` can perform `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeleteMember`, etc., an attacker who is later removed as a member can still have contributed one of the K signatures needed to authorize a transfer of funds out of the multisig account, effectively letting a request pass with fewer *live, currently-trusted* confirmers than the configured threshold. This matches the report's "Critical: a multisig request executed below threshold" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires (a) an outstanding, unconfirmed-to-completion request that a member has partially confirmed, and (b) that confirming member subsequently being removed before the request reaches full confirmation or is deleted. This is a realistic real-world sequence (revoking a compromised or departing signer while other multisig business is in flight) rather than a contrived edge case, and it requires no privileged action by the exploiting party beyond having been a legitimate member at the time of confirming — the same "few whitelisted/trusted parties can trigger it" profile the original report was scored Medium for.

### Recommendation
In `delete_member` (and `DeleteKey` in multisig v1), iterate all outstanding requests' confirmation sets (not just those authored by the removed member) and strip the removed member's identity from every confirmation set; alternatively, revalidate at `confirm()`/execution time that every entry in the stored confirmation set is still `self.members.contains(...)` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new([A, B, C, D], 2)`.
2. A calls `add_request(transfer_to_attacker)` → request R (0 confirmations).
3. B calls `confirm(R)` → confirmations = `{B}` (1 of 2 needed).
4. Members submit and confirm a separate request `DeleteMember { member: B }` (e.g. because B's key leaked) — `delete_member` executes, removing B from `self.members`, but does not touch confirmations on R.
5. C calls `confirm(R)` → `confirmations.len() + 1 == 2 >= num_confirmations`, so R executes the transfer — even though one of its two "confirmations" (B) is no longer a member of the multisig at execution time. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** multisig2/src/lib.rs (L356-374)
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
