### Title
Multisig confirmations from removed keys/members are never purged, allowing requests to execute below the live confirmation threshold - ([File: multisig/src/lib.rs], [File: multisig2/src/lib.rs])

### Summary
Both `multisig` and `multisig2` contracts implement a `K`-of-`N` confirmation scheme where a `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, etc. request is executed once `confirmations.len() + 1 >= num_confirmations`. When a signing key (`multisig`) or member (`multisig2`) is removed via the `DeleteKey`/`DeleteMember` action, the contract only purges **requests originated by** that key/member — it never purges **confirmations already cast by** that key/member on other, still-pending requests. As a result, a stale confirmation recorded by a revoked signer keeps counting toward the quorum of any pending request, letting the remaining live signers execute a request (including a `Transfer` of NEAR) with fewer live confirmations than `num_confirmations` actually requires.

### Finding Description
`confirm()` reads the confirmation set for a request and executes it once the threshold is met: [1](#0-0) 

`DeleteKey` removal logic only cleans up requests **created (signed)** by the deleted key, and only removes `num_requests_pk` bookkeeping for that key — it does not scan other requests' `confirmations` sets to strip out entries belonging to the deleted key: [2](#0-1) 

The `multisig2` contract has the identical pattern in `delete_member`, which only removes requests where `r.member == member` (i.e., requests the removed member *created*), and never touches the `confirmations` map of other pending requests that the removed member had already confirmed: [3](#0-2) 

`confirm()` in `multisig2` unconditionally counts every entry already present in the `confirmations` set toward the threshold, with no check that each entry still corresponds to a current member: [4](#0-3) 

This is structurally the same bug class as the reported SDLPool issue: a narrower-scoped grant (a per-lock `approve` / here, a per-request `confirmation`) is not revoked when the broader authorization (`operatorApprovals` / here, multisig membership) is revoked, so the stale grant remains usable. Here the binding broken is: **confirmations counted == live members who confirmed**. After a member/key removal, this becomes **confirmations counted > live members who confirmed**, because the ex-member's confirmation is still present in the `HashSet` and is summed toward `num_confirmations`.

### Impact Explanation
This lets a multisig request — including a `Transfer` action that moves NEAR out of the contract's account, or an `AddKey`/`AddMember`/`DeployContract` action — execute with fewer live, currently-authorized confirmations than the configured `num_confirmations` threshold. This is a direct authorization-threshold bypass: "a multisig request executed below threshold," which is explicitly listed as a Critical impact.

### Likelihood Explanation
This requires no attacker-controlled bug in cryptography or race conditions — it is deterministic and reachable through the contract's normal, documented flow: any request that gathers partial confirmations, followed by a legitimate member-removal request (which is itself a common operational action, e.g. rotating a compromised key), leaves the removed signer's confirmation live on any other still-pending request. Any subsequent confirmer completes the now-artificially-lowered quorum. No privileged access beyond normal member/key confirmation rights is needed by the exploiting parties (the remaining legitimate signers, potentially colluding with or without the removed member's knowledge), and the removed member's signature was legitimately obtained before removal — it is simply never invalidated.

### Recommendation
When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate over **all** pending requests' `confirmations` sets (not just requests created by the removed key/member) and remove any entry belonging to the removed key/member. Alternatively, validate at `confirm()`/execution time that every entry in a request's `confirmations` set still corresponds to a currently active key/member, discarding stale ones before comparing against `num_confirmations`.

### Proof of Concept
Using `multisig2` semantics (analogous steps apply to `multisig`):
1. Deploy with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request(R)` where `R` is `Transfer { amount }` to an attacker-controlled receiver — 0 confirmations so far.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1/3).
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2/3, not yet executed).
5. Separately, the group legitimately removes `C` from the multisig via a `DeleteMember { member: C }` request that reaches its own quorum and executes `delete_member` — per [3](#0-2) , this removes `C` from `members` and purges requests *created* by `C`, but request `R` (created by `A`) is untouched, so `confirmations[R]` still contains `C`.
6. `D` (a remaining live member) calls `confirm(R)` → `confirmations[R].len() + 1 == 3 >= num_confirmations`, so `execute_request(R)` fires and the `Transfer` executes — even though only `B` and `D` are live confirming members; the third "confirmation" came from `C`, who was already removed from the multisig at the time of execution.

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
