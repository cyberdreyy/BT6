### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute with fewer than `num_confirmations` live approvers - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in both `multisig2/src/lib.rs` and `multisig/src/lib.rs` authorizes execution of a pending request purely by comparing the *size* of the stored `confirmations` set to `num_confirmations`. When a member is removed via `DeleteMember`/`DeleteKey`, the code only purges confirmation records for requests that member *originated*, not confirmation entries that member left on *other* requests it merely approved. Those stale entries keep counting toward the threshold, letting a request execute even though the number of currently-live members who actually approved it is below `num_confirmations`.

### Finding Description
The binding that must hold is:

`confirmations.len()` at execution time `==` number of confirmations cast by accounts/keys that are still members of `self.members` at that time.

`confirm()` never re-validates this equivalence: [1](#0-0) 

`delete_member()` only cleans up requests *originated by* the removed member — it filters `self.requests` by `r.member == member`, then removes `self.confirmations` only for those matched request IDs: [2](#0-1) 

It never scans the `confirmations: LookupMap<RequestId, HashSet<String>>` map for entries where the removed member's serialized identity appears as a *confirmer* (but not the *originator*) of some other still-pending request. Those stale confirmations remain in the set and are subsequently `+1`'d against by any live member’s `confirm()` call: [3](#0-2) 

The identical structural flaw exists in the legacy `multisig` contract's `DeleteKey` action, which filters by `r.signer_pk == pk` (the request's originating key) when cleaning confirmations, leaving confirmations given by that key on other requests intact: [4](#0-3) [5](#0-4) 

This is structurally the same class of bug as the DYAD `VaultManagerV2::liquidate` finding: a threshold/solvency check (`cr >= MIN_COLLATERIZATION_RATIO` there, `confirmations.len() >= num_confirmations` here) is evaluated against a stored value that has silently diverged from the real, current state (non-kerosene collateral vs. minted DYAD there; live multisig membership vs. recorded confirmations here) because no invalidation/reconciliation step exists when the underlying state changes (collateral price drop there, member removal here).

### Impact Explanation
A `MultiSigRequest` (including high-impact actions such as `Transfer`, `AddKey`/`AddMember`, `FunctionCall`, or `DeployContract`) can be executed with strictly fewer live-member approvals than the configured `num_confirmations`, i.e. below the governance threshold the multisig was configured to enforce. This falls squarely under the listed Critical impact: "a multisig request executed below threshold." Funds can be moved, or new keys/members can be added, without the intended quorum of currently-authorized signers actually approving in real time.

### Likelihood Explanation
This requires only ordinary, unprivileged multisig operation flow that already occurs in practice: (1) member D confirms (but does not originate) a pending request R created by member A; (2) at some later point the members (through ordinary governance, e.g. offboarding D) execute a `DeleteMember`/`DeleteKey` request removing D; (3) R is still pending and D's confirmation on R was never purged because `delete_member`/`DeleteKey` only clears confirmations for requests D itself created. Any remaining live member(s) can then push R over the (now effectively lowered) threshold. No special privilege beyond being an ordinary current member is needed, and member turnover (removing an ex-employee's key, rotating a compromised key, etc.) is exactly the intended, expected use of `DeleteMember`. Likelihood is Medium-to-High given member rotation is a routine multisig operation and stale pending requests are common (the contract even documents an `active_requests_limit`/`REQUEST_COOLDOWN` acknowledging requests can sit for a while).

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` handling in `multisig/src/lib.rs`), iterate over **all** pending requests' `confirmations` sets (not just requests originated by that member) and remove the departing member's entry from each. Alternatively, validate at `confirm()`/execution time that every entry in a request's `confirmations` set still corresponds to a current member of `self.members`, discounting any that do not, before comparing the count to `num_confirmations`.

### Proof of Concept
Conceptual sequence (multisig2, `num_confirmations = 3`, members = {A, B, C, D}):
1. Member A calls `add_request` creating `MultiSigRequest R` (e.g., `Transfer` to an attacker-controlled account). `confirmations[R] = {}`.
2. Member D calls `confirm(R)`. Now `confirmations[R] = {D}` (len 1, `1+1 < 3`, not yet executed).
3. Separately, members execute a `DeleteMember { member: D }` request (a normal governance action, e.g. offboarding). `delete_member()` runs: it filters `self.requests` for entries with `r.member == D` — but `R.member == A` (A is the originator), so `R`'s confirmations are **not** touched; only `D`'s own originated requests (if any) get cleared. `D` is removed from `self.members` and its access key deleted.
4. Member A calls `confirm(R)` (A adds its own confirmation): `confirmations[R] = {D, A}`, len 2, `2+1 < 3`, not executed yet.
5. Member B calls `confirm(R)`: `confirmations.len() as u32 + 1 = 3 >= self.num_confirmations (3)` → `execute_request(R)` fires the `Transfer`.

At execution time only A and B are actual live members who approved — D was removed in step 3 — yet the code treats D's stale pre-removal confirmation as still valid, so the request executes with only 2 live approvals against a configured 3-of-4 threshold.

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
