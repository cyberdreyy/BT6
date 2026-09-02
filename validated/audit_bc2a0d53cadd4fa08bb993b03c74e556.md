## Title
Stale confirmations from removed multisig members still count toward the confirmation threshold, allowing request execution below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
The Morpho report's underlying bug class is a *stale cached state vs. current live state* mismatch used in a threshold/authorization check. The direct analog in this repo is in the `multisig` and `multisig2` contracts: when a member is removed via `DeleteMember`/`DeleteKey`, the contract only purges pending *requests authored by* that member — it does not purge *confirmations already cast by* that member on other still-pending requests. As a result, a request can later execute using a confirmation count that includes a vote from an account that is no longer a member, breaking the invariant that `confirmations counted == confirmations from current live members`.

### Finding Description
`confirm()` decides execution purely by counting entries in the `confirmations` set for a request and comparing against `num_confirmations`: [1](#0-0) 

When a member is deleted, `delete_member` only removes requests where the removed member is the *author* (`r.member == member`), and clears confirmations for *those* requests only. It does not scan all other pending requests to strip out any confirmation the removed member may have already cast on them: [2](#0-1) 

The same pattern exists in the older `multisig` contract's `DeleteKey` action, which likewise filters only by `r.signer_pk == pk` (requests *authored* by that key) before clearing confirmations, leaving that key's confirmations on other pending requests untouched: [3](#0-2) 

Consequently, `confirmations.len()` for a still-pending request can contain an entry from an account/key that has since been removed from `members` (or, in `multisig` v1, deleted as an access key). Since `confirm()` never re-validates that *previously stored* confirmations still belong to current members — it only checks the *current* caller via `current_member()` — the stale entry is silently counted toward the threshold forever, until that particular request is confirmed/executed or deleted.

This breaks the intended equality:
`confirmations_counted == confirmations_from_current_live_members`

### Impact Explanation
This maps to the Critical bucket: "a multisig request executed below threshold." A request (e.g., a `Transfer` or `FunctionCall` draining funds) that was only ever approved by `num_confirmations - 1` *current* members plus one stale vote from a now-removed member can still execute, because the removed member's earlier confirmation is never invalidated. This effectively lowers the real security threshold of the multisig below its configured `num_confirmations` whenever membership changes while requests are outstanding — a direct violation of the K-of-N custody guarantee the contract is supposed to provide.

### Likelihood Explanation
This does not require any attacker to hold contract-owner or foundation privileges, nor does it require a redeploy or social engineering. It only requires the ordinary operational sequence: (1) a member confirms a pending request, (2) that member is later removed (a routine event — key rotation, offboarding, revoking a compromised key), while (3) the original request remains pending. No special malicious action beyond normal usage is needed for the stale-confirmation count to persist; a malicious member could deliberately engineer this sequence (confirm a large transfer, then get themselves "cleanly" removed for unrelated reasons, or arrange to be removed) to preserve their vote's effect after they no longer control any live key/account.

### Recommendation
When executing `delete_member` (or `DeleteKey`), iterate over **all** pending requests' confirmation sets (not just requests authored by the removed member) and strip any confirmation entry matching the removed member/key. Alternatively, validate at `confirm()`-time (when tallying) that every stored confirmation entry still corresponds to a current member of `self.members`, discarding stale ones before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` for `MultiSigRequestAction::Transfer { amount }` to an attacker-controlled account → `request_id = R1`.
3. `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (1 of 3).
4. Separately, members legitimately create and confirm a `DeleteMember { member: B }` request (e.g. because B's key needs rotation) — `delete_member` executes, removing `B` from `members`, but only scans/removes requests authored by `B`; `confirmations[R1] = {B}` is left untouched since `R1` was authored by `A`, not `B`.
5. `C` calls `confirm(R1)` → `confirmations[R1].len() + 1 == 2`. If `num_confirmations` were 2 (or after a `SetNumConfirmations` change, or in a smaller-N configuration), `R1` now executes the transfer, counted as satisfied by `{B, C}` even though `B` is no longer a member — i.e., only 1 live member (`C`) plus 1 stale, non-member confirmation authorized the transfer, executing below the intended live-member threshold. [4](#0-3) [5](#0-4)

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
