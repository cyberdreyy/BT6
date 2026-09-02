## Title
Stale confirmations from removed multisig members allow a request to execute below the configured approval threshold — (multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm()` counts entries in the `confirmations` set for a request without verifying that each entry still belongs to a current member. When a member is removed via `DeleteMember` (or `DeleteKey` in the legacy `multisig` contract), the cleanup logic only purges pending requests that member *created*; it does not scrub confirmations that member previously cast on requests created by other members. Those stale confirmations remain counted toward `num_confirmations`, so a request can execute after receiving approvals from fewer currently-valid members than the configured threshold — the exact "counted confirmations vs. live members" binding break described in the external report.

### Finding Description
`confirm()` simply compares the size of the stored confirmation set to `num_confirmations`: [1](#0-0) 

The confirmation set (`self.confirmations: LookupMap<RequestId, HashSet<String>>`) stores the string identity of whoever confirmed, and is never re-validated against the *current* `members` set at confirm/execute time — only the confirming caller in the current call is checked to be a member via `assert_valid_request` → `current_member()`.

When a member is removed, `delete_member()` only cleans up requests that member *authored* (`r.member == member`, where `member` on `MultiSigRequestWithSigner` records the request creator, not confirmers): [2](#0-1) 

Any confirmation that removed member previously cast on a request *created by someone else* is left untouched in `self.confirmations`, even though `self.members.remove(&member)` has already run. The legacy `multisig` contract has the identical gap in `DeleteKey` handling, which filters by `r.signer_pk == pk` (request creator) only: [3](#0-2) 

Because `confirm()` never filters `confirmations` against the live `members` set before comparing to `num_confirmations`, a stale confirmation from a departed member is functionally equivalent to a live vote, letting the threshold be satisfied with fewer than `num_confirmations` distinct *current* members.

### Impact Explanation
This breaks the intended binding: `confirmations recorded == approvals from live members`. A malicious or compromised member's prior confirmation on a request they did not create effectively survives their removal from the wallet, and can combine with one fresh confirmation from another current member to hit the threshold and execute the request (e.g. `Transfer`, `AddKey`/`AddMember`, `FunctionCall`) — this is a "multisig request executed below threshold" scenario, listed as Critical impact.

### Likelihood Explanation
The precondition is realistic and requires no privileged action beyond normal wallet operation: a member confirms a pending request created by another member (a common, legitimate action), and is later removed via the standard `DeleteMember`/`DeleteKey` request (e.g. because their key was reported compromised — the same social-engineering pretext used in the original report). No special timing or race condition is needed; the stale confirmation persists indefinitely until the request is deleted or executed.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), iterate over **all** pending requests' confirmation sets and strip the departing member's entry, not just requests they authored. Alternatively, change `confirm()`/execution to recompute the valid-approval count by intersecting the stored `confirmations` set with the current `self.members` set (or store confirmations keyed by member reference and validate against live membership at execute time), matching the Gnosis MultiSigWallet approach of iterating over live owners in `_execute()` cited by the original report.

### Proof of Concept
1. Initialize `multisig2` with `num_confirmations = 2`, members `{M1, M2, M3}`.
2. `M1` calls `add_request` to create request `R` (e.g., `Transfer` draining the wallet) — `R` is *not* confirmed yet (`confirmations = {}`).
3. `M2` calls `confirm(R)` → `confirmations = {M2}` (1 < 2, pending).
4. Separately, `M1` and `M3` create+confirm a `DeleteMember { member: M2 }` request (e.g. claiming M2's key was compromised) — reaches threshold 2, executes: `self.members.remove(&M2)` runs, but since `M2` never *created* any request, `delete_member`'s cleanup loop (`multisig2/src/lib.rs:362-371`, filtering on `r.member == member`) does not touch `R`; `R.confirmations` still contains `M2`.
5. `M1` and `M3` add a new member `M4` (2/3 current members: `M1`,`M3`).
6. `M3` (now a valid current member, never having confirmed `R`) calls `confirm(R)` → `confirmations = {M2, M3}`, size 2 ≥ `num_confirmations` (2) → `R` executes, transferring funds — even though `M2` is no longer a member and only one *currently valid* member (`M3`) actually approved `R`.

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
