### Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` implement a k-of-n confirmation scheme where `confirm()` counts entries in a per-request `confirmations` set against `self.num_confirmations` to decide whether to execute a request. When a member/key is removed (`DeleteKey` in `multisig`, `DeleteMember` in `multisig2`), the removal logic only purges requests **created by** that member/key; it does not scrub that member's **confirmation** from other, still-pending requests that they had previously confirmed but did not create. This mirrors the Unlock `freeTrial` finding's underlying bug class: a value/claim (here, a "confirmation" credit toward the execution threshold) that was validly granted while its owner was a member survives revocation of the granting identity and can still be redeemed (counted) later — i.e. "confirmations counted versus live members" diverges from reality.

### Finding Description
In `multisig2/src/lib.rs`:
- `confirm()` (lines 294-315) checks `confirmations.len() as u32 + 1 >= self.num_confirmations` and, if satisfied, executes the request via `execute_request`.
- `delete_member()` (lines 356-379) removes:
  - requests where `r.member == member` (requests the removed member itself created) along with their confirmation sets,
  - the member's `num_requests_pk` entry,
  - the member from `self.members`,
  - the access key, if key-based.

It never iterates `self.confirmations` to strip the removed member's `String` from confirmation sets of requests **created by other members**. The same pattern exists in `multisig/src/lib.rs`'s `DeleteKey` handling (lines 198-216), which only removes requests where `r.signer_pk == pk` (i.e. requests created by that key), not confirmations that key placed on other requests. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The binding that should hold is:
`confirmations_counted(request) == confirmations_from_currently_live_members(request)`

After a member is removed while having an outstanding confirmation on a request created by someone else, this becomes:
`confirmations_counted(request) > confirmations_from_currently_live_members(request)`

Because a stale confirmation from a now-removed member is still present in the `HashSet` and is counted by `confirm()`, the remaining live members need fewer *additional* confirmations than the configured `num_confirmations` intends — the effective threshold is silently lowered by however many confirmations were left behind by removed members.

### Impact Explanation
This breaks a threshold/authorisation boundary explicitly listed as in-scope ("a multisig request executed below threshold"). If a member is removed (e.g., because their key was compromised, they left the organization, or as routine membership rotation) after having confirmed a pending `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request that other members are still working to approve, that stale confirmation persists and effectively counts toward execution. The remaining members may then execute the request with fewer live confirmations than `num_confirmations`, moving NEAR (or granting access keys / deploying code) with authorization below the configured threshold — a Critical-severity impact per the scope's own definition ("a multisig request executed below threshold").

### Likelihood Explanation
The precondition (an outstanding confirmed-but-not-yet-executed request existing at the time a member is removed) is a normal, expected operational sequence for any active k-of-n multisig with several concurrently open requests — it does not require any privileged action beyond what member management already permits, and no code/deployment misconfiguration is needed. The attacker scenario is: a compromised/removed member deliberately confirms several high-value pending requests just before (or as) they are removed, guaranteeing their stale confirmation persists and lowers the effective threshold for whichever request executes next.

### Recommendation
When removing a member/key (`delete_member` / `DeleteKey`), iterate over all active requests' confirmation sets and remove the departing member's entry from every set, not just the sets of requests they created. Alternatively, validate at `confirm()`/execution time that every counted confirmation still belongs to a current member (`self.members.contains(...)`) before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `B` calls `add_request` on `receiver_id = current_account_id`, action `Transfer { amount: X }` → `request_id = 0`.
3. Member `D` (not the creator) calls `confirm(0)` → `confirmations = {D}` (1 of 3).
4. Members execute a separate multisig flow (`AddMember`/`DeleteMember` action) that reaches quorum and removes `D` via `DeleteMember { member: D }`. `delete_member` only deletes requests where `r.member == D` (i.e., requests `D` created) — request `0` was created by `B`, so its confirmation set `{D}` is left untouched. [5](#0-4) 
5. Members `A` and `C` now confirm request `0`: `confirmations.len() + 1 >= 3` becomes true after just these two additional confirmations, because `D`'s stale confirmation is still counted. [6](#0-5) 
6. Request `0` executes with confirmations `{D, A, C}`, even though `D` is no longer a member — i.e., only 2 live members (`A`, `C`) out of the required 3 actually approved it at execution time.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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
