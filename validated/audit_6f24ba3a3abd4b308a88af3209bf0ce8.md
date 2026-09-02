## Analysis

I found a concrete analog of the reported bug class. The external report's core issue is that a value used to authorize a privileged action (claimable `lpETH`) is computed from stale/contaminated state rather than the actual, currently-valid balance — allowing an unentitled party to benefit from state that should not count toward them. The closest reachable analog in this repository is in `multisig2/src/lib.rs`: confirmations are counted as raw strings in a `HashSet` and are never invalidated/reconciled against the *current* live membership set when a member is removed, except for requests where the removed member was the *original requester* (`delete_member` only purges requests keyed by `r.member == member`, i.e. requests that member itself created — not confirmations that member gave on other, still-pending requests). This breaks the equality: `num_confirmations counted == num_live_members_who_confirmed`.

### Title
Multisig executes requests with stale confirmations from removed members, bypassing the confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations stored as strings in a `HashSet<String>` per request, comparing `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0) . When a member is removed via `DeleteMember`/`delete_member`, the code only removes **requests originally created by that member** and their associated confirmation sets — it does not scan all other pending requests to strip that member's confirmation from them [2](#0-1) . As a result, a confirmation given by a member on request X survives in `confirmations[X]` even after that member is deleted from `self.members`, and it still counts toward reaching `num_confirmations` when a later member calls `confirm(X)`.

### Finding Description
The binding that should hold is: *a request executes only when at least `num_confirmations` **currently valid/live** members have confirmed it* — i.e. `confirmations_counted == confirmations_from_live_members`. 

In practice:
- `confirm()` re-validates only the *new* confirmer via `current_member()` [3](#0-2) , but reads the raw stored `HashSet<String>` of confirmations without re-checking that each previously-recorded confirmer is still in `self.members`.
- `delete_member()` removes confirmation sets only for requests where `r.member == member` (i.e., requests the removed member itself *created*), not requests the removed member merely *confirmed* [4](#0-3) .
- Consequently, if member M confirms request R (created by someone else), and M is later removed via a separate `DeleteMember` request, R's confirmation set in storage still contains M's entry. The next confirmer on R benefits from M's stale confirmation counting toward the threshold, even though M is no longer an authorized signer.

This is directly analogous to the report's core defect: a recorded/cached value (confirmation count) is trusted as equal to a live, authoritative quantity (currently valid confirmers) without being reconciled at the moment of use — precisely the "confirmations counted versus live members" identity crossing called out in the rules.

### Impact Explanation
This allows a multisig request (e.g., `Transfer`, `AddKey` granting a full-access key, `FunctionCall`, `DeployContract`) to be executed with fewer than `num_confirmations` **currently authorized** members effectively backing it. Concretely: with `num_confirmations = 3`, if a since-removed member M had earlier confirmed a still-pending Transfer request, only 2 *live* members need to confirm afterward for the transfer to execute — a multisig request executed below the intended live-member threshold. This falls under the Critical impact category ("a multisig request executed below threshold"), since NEAR can be moved, or a rogue full-access key added, by fewer authorized parties than the multisig configuration requires.

### Likelihood Explanation
This requires no privileged actor and no malicious validator: any legitimate scenario where a member confirms a pending request, is later removed as part of normal membership rotation, and the original request remains outstanding (within its lifetime, no cooldown prevents this) triggers the flaw. It does not depend on ignoring documented initialization; it is purely a bookkeeping gap during a documented, expected operation (member removal). Because member turnover and multiple concurrently pending requests are a normal operational pattern for a K-of-N multisig, likelihood is non-trivial, though it does require a specific ordering (confirm, then remove that confirmer, then let the request complete) that a benign environment would need to encounter/an attacker with any key/account membership (even non-removed) could exploit by acting as the final confirmer.

### Recommendation
When removing a member in `delete_member`, iterate over **all** pending requests (not just those keyed by `r.member == member`) and strip the removed member's entry from each request's confirmation `HashSet`. Alternatively, when counting confirmations in `confirm()`, filter the stored confirmation set to only those entries still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request` to create a `Transfer` request `R` (receiver arbitrary, e.g. an attacker-controlled account).
3. `B` calls `confirm(R)` → confirmations = {A, B} (2 confirmations, not yet ≥ 3, so request stays pending) [5](#0-4) .
4. Separately, `D` proposes and the group confirms a `DeleteMember { member: B }` request; it executes, calling `delete_member`, which removes `B` from `self.members` but does **not** touch `confirmations[R]` because `R.member == A`, not `B` [6](#0-5) .
5. `C` (still a live member) calls `confirm(R)`. `confirmations[R]` still contains `{A, B}`; adding `C` makes `len()+1 == 3 >= num_confirmations`, so `execute_request` fires the `Transfer` [7](#0-6) .
6. The transfer executes with only 2 currently-live members (A and C) having actually authorized it post-removal, plus one stale confirmation from removed member B — below the intended 3-live-member threshold. [8](#0-7) [9](#0-8)

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
