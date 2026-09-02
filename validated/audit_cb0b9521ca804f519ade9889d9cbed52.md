## Analysis

The reported bug class is: a privileged/authorized value (`sendParam.amountLD`) is trusted and acted upon without being cross-checked against the actual computed/authoritative value (`debtOut`), letting an attacker break a custody binding. The analogous binding in this repository's multisig contracts is: **confirmations counted toward the execution threshold must equal confirmations cast by currently-live members**. This binding is broken in `multisig2/src/lib.rs` (and identically in `multisig/src/lib.rs`).

### Root cause

`confirm()` decides whether to execute a request purely by comparing the *size* of the stored confirmation set to `num_confirmations`: [1](#0-0) 

Membership removal is handled by `delete_member()`, which only purges requests and confirmations **for requests originally created by the removed member** (`r.member == member`). It never scans other pending requests' `confirmations` sets to strip out the removed member's earlier confirmations: [2](#0-1) 

So if member `A` confirms a pending request they did not create, and is later removed via a separate `DeleteMember` request, `A`'s entry remains in `self.confirmations` for that still-pending request. When enough *live* members subsequently confirm, `confirmations.len() + 1 >= num_confirmations` is satisfied using the stale confirmation from a no-longer-authorized member, and the request executes (`Transfer`, `FunctionCall`, `AddKey`, etc.) below the intended live-member threshold.

The v1 contract has the identical pattern: `DeleteKey` only clears confirmations/requests for requests the key itself created, not confirmations that key cast on other requests: [3](#0-2) [4](#0-3) 

### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`delete_member` (v2) / `DeleteKey` (v1) only removes requests and confirmations for requests *originated* by the member/key being removed. Confirmations that member cast on other, still-pending requests are never purged from the `confirmations` map. `confirm()` counts set size against `num_confirmations` without validating that every entry still belongs to a current member, so a request can execute using a phantom confirmation from a removed member.

### Finding Description
The binding that must hold is: `confirmations_counted(request) == confirmations_from_live_members(request)`. `execute_request`/`confirm` treats `self.confirmations.get(&request_id).len()` as ground truth for authorization [5](#0-4) , but `delete_member` only filters requests by `r.member == member` (the request's *creator*), leaving any confirmation records the removed member left on *other* requests untouched [6](#0-5) . There is no membership check inside `confirm()` that re-validates each existing confirmation entry against the current `self.members` set.

### Impact Explanation
This lets a multisig execute a request with fewer genuinely authorized (live) confirmations than `num_confirmations`, i.e. "a multisig request executed below threshold" — explicitly a Critical-severity outcome, since it can move funds (`Transfer`), deploy/upgrade code (`DeployContract`), or grant access keys (`AddKey`) using a confirmation from an account that is no longer trusted.

### Likelihood Explanation
This requires no attacker-controlled input manipulation beyond normal multisig usage: any legitimate scenario where a member confirms a request they didn't create, and is subsequently removed (a routine key-rotation/offboarding action) before that pending request reaches quorum, triggers the bug. It does not require a compromised key, a redeploy, or social engineering — it's a straightforward sequencing issue in the shipped contract logic.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), iterate all pending requests' confirmation sets and strip any entry matching the removed member/key, not just requests they created. Alternatively, validate on `confirm()` (and before executing) that every recorded confirmation still corresponds to a current member of `self.members`, recomputing the effective count from only live members.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `X` (any member) calls `add_request` creating request `R` (e.g., `Transfer` of funds) — not auto-confirmed.
3. `A` calls `confirm(R)` → `confirmations = {A}`.
4. `B` calls `confirm(R)` → `confirmations = {A, B}` (still below threshold 3, so no execution).
5. Separately, a fully-confirmed `DeleteMember { member: A }` request executes, removing `A` from `self.members`. Because `delete_member` only removes requests where `r.member == A` (i.e., requests *created by* `A`), request `R` (created by `X`) is untouched, and `A`'s stale entry stays in `confirmations`.
6. `C` calls `confirm(R)` → `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` runs, transferring funds, using the phantom confirmation from `A`, who is no longer a multisig member. [1](#0-0) [2](#0-1)

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
