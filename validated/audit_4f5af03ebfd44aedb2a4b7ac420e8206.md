I found a confirmed vulnerability class matching the required binding: confirmations counted versus live members diverge because `delete_member`/`DeleteKey` only purges requests *originated by* the removed member, not confirmations that member cast on *other* still-pending requests.

### Title
Stale confirmations from removed multisig members are still counted toward the threshold, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`confirm()` counts entries in the `confirmations` set for a request and executes it once the count reaches `num_confirmations`. When a member is removed via `MultiSigRequestAction::DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), the cleanup logic in `delete_member`/`execute_request::DeleteKey` only scrubs *requests originated by* that member — it never removes that member's confirmation entries recorded on *other* pending requests they didn't originate. As a result, a confirmation cast by a member who is later removed remains counted forever, letting a request execute with fewer live, current confirmations than the configured threshold.

### Finding Description
`delete_member` in `multisig2/src/lib.rs`: [1](#0-0) 

only filters `self.requests` where `r.member == member` (i.e., requests that member *added*) and clears confirmations for those. It never iterates `self.confirmations` to strip the removed member's entry from *other* pending requests. The equivalent `DeleteKey` branch in the older `multisig/src/lib.rs` has the identical gap: [2](#0-1) 

Meanwhile, `confirm()` blindly trusts the stored `confirmations` set size against `num_confirmations` without re-validating that each entry still corresponds to a live member: [3](#0-2) [4](#0-3) 

The binding that should hold is:
`count(confirmations[request_id]) == count(distinct *live* members who confirmed)`

But after a `DeleteMember`/`DeleteKey` execution, a stale confirmation from the now-removed member remains in `confirmations[other_request_id]`, so:
`count(confirmations[request_id]) > count(distinct live members who confirmed)`

This means a request can reach the `num_confirmations` threshold with one fewer *currently authorized* signer than intended, effectively lowering the real security threshold from K-of-N to (K-1)-of-(N-1) for any request that a soon-to-be-removed member had already confirmed before being deleted.

### Impact Explanation
This directly matches the "multisig request executed below threshold" Critical impact category. Funds (`MultiSigRequestAction::Transfer`), key rotations (`AddKey`), contract upgrades (`DeployContract`), and even membership changes can all be executed with a confirmation count that includes a party no longer trusted/authorized by the group, undermining the fundamental K-of-N custody guarantee of the multisig.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires (a) a member confirming a pending request they didn't create, then (b) that member later being removed via `DeleteMember`/`DeleteKey` while the earlier request remains pending, and (c) the remaining threshold being met by other still-live members. Since members can be maliciously colluding, compromised, or simply rotated out during normal operational key rotation, this is a realistic operational sequence rather than a purely theoretical one, particularly because the contract's own confirmation-count check gives no indication that a stale vote is being relied upon.

### Recommendation
When removing a member (`delete_member`/`DeleteKey`), iterate all pending requests' confirmation sets (not just requests they originated) and remove that member's entry. Alternatively, revalidate on `confirm()`/execution that every entry in `confirmations[request_id]` still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy a multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R1` (e.g., `Transfer` to an attacker-controlled account).
3. `D` calls `confirm(R1)` — `confirmations[R1] = {D}` (1 of 3).
4. Separately, members confirm and execute a `DeleteMember { member: D }` request (this passes `delete_member`'s check since `members.len() - 1 = 3 >= num_confirmations = 3`). Since `D` did not originate `R1`, `delete_member`'s cleanup loop (filtering `r.member == member`) does not touch `confirmations[R1]`, which still contains `D`.
5. `B` and `C` confirm `R1`: `confirmations[R1] = {D, B, C}` → `len() == 3 >= num_confirmations`, so `R1` executes via `execute_request`.
6. `R1` executed with confirmations from only 2 genuinely current members (`B`, `C`) plus a stale vote from removed member `D` — below the intended 3-of-N live-member threshold. [5](#0-4)

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
