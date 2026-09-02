Confirmed. Analysis of `delete_member` in `multisig2/src/lib.rs` shows the vulnerability: when a member is removed, `delete_member` only removes *requests originated by* that member (`r.member == member`), and it removes `num_requests_pk` entries for that member — but it does **not** scan `self.confirmations` for other, still-pending requests that the removed member had already **confirmed** as a co-signer.### Title
Confirmations of removed multisig members remain valid and count toward the confirmation threshold, allowing requests to execute with fewer live members than `num_confirmations` - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
Both the `multisig` and `multisig2` contracts allow a request to execute as soon as `confirmations.len() + 1 >= num_confirmations`. When a member is deleted from the multisig (via `DeleteMember`/`DeleteKey`), the contract only purges requests and `num_requests_pk` entries for the request-originator equal to the removed member; it does not scan the `confirmations` set of *other* pending requests to strip out confirmations previously cast by the now-removed member. This breaks the intended equality "confirmations counted == live members who confirmed," letting a request execute even though one or more of the counted confirmers are no longer valid multisig signers.

### Finding Description
`confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the stored `confirmations: HashSet<String>` for that `request_id` against `num_confirmations`: [1](#0-0) 

`delete_member()` is the only place that removes members and it explicitly cleans up:
1. `requests` created by the removed member (`r.member == member`)
2. `num_requests_pk` for the removed member
3. `members` set

It does **not** walk `self.confirmations` for other requests (created by different members) that the removed member had already confirmed: [2](#0-1) 

Consequently, the invariant the contract is supposed to enforce — "a request executes only once `num_confirmations` *live* members have approved it" — is broken to: "a request executes once `num_confirmations` entries exist in a stale confirmations set, some of which may belong to accounts/keys that are no longer members."

Concrete attack path:
1. Multisig is `K`-of-`N` (e.g. 3-of-4): members `A, B, C, D`.
2. Member `E`'s request `R` (created by `A`, say) is confirmed by `B` (1 confirmation, needs 3).
3. Separately, a `DeleteMember` request removes `B` (e.g. because `B`'s key/account was compromised, resigned, or is being replaced) with 3 confirmations from `A, C, D`. `delete_member` removes `B` from `members`, deletes any request *originated* by `B`, and clears `num_requests_pk[B]`, but request `R`'s confirmation set still contains `B`.
4. `A` and `C` now confirm `R`. `confirmations.len()` becomes `3` (`B, A, C`) which is `>= num_confirmations (3)`, so `R` executes — even though `B` is no longer a member and only 2 *live* members (`A`, `C`) plus the request's own creator confirmed it. Effectively the multisig executed a request with 2 live confirmers instead of the required 3.
5. The identical logic and gap exist in the legacy `multisig` contract's `DeleteKey` action, which likewise only purges requests originated by the deleted key and its `num_requests_pk` entry, not confirmations the key already cast on other pending requests: [3](#0-2) [4](#0-3) 

This is the direct analog of the reported bug class: a state cached at proposal/confirmation time (fractions deposited / a confirmation) is treated as still authoritative for a later global action (buyout start / request execution) without being reconciled against the current, real state (actual fractions held / actual live membership).

### Impact Explanation
This crosses the "multisig request executed below threshold" boundary explicitly called out as Critical impact: `Transfer`, `AddKey`/`FunctionCallPermission`, `FunctionCall`, `AddMember`, `DeleteMember`, `SetNumConfirmations` and `SetActiveRequestsLimit` requests can all be pushed through with fewer genuinely live approvals than the configured `K`. Since these multisig contracts are typically used to control access to funds/accounts (e.g. NEAR Foundation/lockup-adjacent tooling), this can lead to unauthorized transfers of NEAR, unauthorized key/membership changes, or lowering the confirmation bar itself — i.e., funds moved or account control changed by a party not entitled to it under the declared K-of-N policy.

### Likelihood Explanation
The precondition is realistic and even routine: multisig operators regularly rotate/remove members (compromised key, employee offboarding, key rotation). Any pending request that has already picked up a confirmation from the member being removed will silently retain that "ghost" confirmation. No special timing or malicious cooperation from the removed member is required — the remaining members do not need to be aware that a stale confirmation is being counted, since `get_confirmations` / `get_num_confirmations` view methods don't distinguish live vs. stale confirmers, and nothing prompts operators to audit outstanding confirmations of a member before removing them. This makes the flaw likely to be triggered accidentally, and trivially exploitable by a malicious about-to-be-removed member who front-loads confirmations on requests before being removed.

### Recommendation
On `DeleteMember` (and legacy `DeleteKey`), iterate over **all** entries in `confirmations` (not just requests originated by the removed member) and remove the removed member's/key's confirmation string/key from every set; if this drops a request's `confirmations.len()` such that execution would no longer meet threshold, leave it pending as intended. Alternatively, revalidate at `confirm()`/execution time that every entry in the stored `confirmations` set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
Rust-style pseudocode extending the existing multisig2 test harness (`multisig2/src/lib.rs` tests module):
```rust
#[test]
fn test_stale_confirmation_survives_member_deletion() {
    // members: alice, bob, and two access keys (K1, K2); num_confirmations = 3
    let mut c = MultiSigContract::new(members(), 3);

    // 1. alice creates request R (e.g. Transfer to some receiver)
    let r_id = c.add_request(MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: 1_000.into() }],
    });

    // 2. bob confirms R -> confirmations = { bob } (len = 1, need 3)
    // switch context to bob, call c.confirm(r_id)

    // 3. Separately, a DeleteMember(bob) request is created & confirmed by
    //    alice, K1, K2 (3 confirmations) and executes, removing bob from
    //    `members`. Note: delete_member only purges requests bob *originated*
    //    and bob's num_requests_pk entry -- confirmations.get(&r_id) still
    //    contains "bob".

    // 4. alice and K1 confirm R.
    // switch context to alice, call c.confirm(r_id)  -> confirmations.len() = 2
    // switch context to K1,   call c.confirm(r_id)  -> confirmations.len() = 3 >= num_confirmations(3)
    //    => request R executes, even though only alice and K1 are still live
    //       members that approved it (bob's stale confirmation is counted).

    // Expected (fixed) behavior: request R should require 3 *live* member
    // confirmations, so it should still be pending after alice+K1 confirm.
}
```
This mirrors the original report's core mechanic — state recorded at one point (confirmation/fractions committed) is trusted unconditionally by a later privileged action (execute/buyout) without checking it against the current, real membership/holdings.

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
