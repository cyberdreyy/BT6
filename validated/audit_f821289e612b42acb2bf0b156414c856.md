### Title
Stale confirmations from removed multisig members/keys are still counted toward the confirmation threshold - ([File: multisig/src/lib.rs], [File: multisig2/src/lib.rs])

### Summary
When a multisig signer (an access-key member in `multisig/src/lib.rs`, or a `MultisigMember` in `multisig2/src/lib.rs`) is removed via `DeleteKey`/`DeleteMember`, the contract only purges **requests that the removed signer itself created**. It never scrubs that signer's **confirmation entries left on other still-pending requests**. Because `confirm()` counts `confirmations.len() + 1 >= self.num_confirmations` without re-validating that every entry in the `confirmations` set still belongs to a current member, a request can later be executed using a stale confirmation from a signer who is no longer trusted — breaking the binding "confirmations counted == live members who actually approved."

### Finding Description
In `multisig/src/lib.rs`, `execute_request`'s `DeleteKey` branch only removes requests whose *original proposer* (`r.signer_pk`) matches the deleted key: [1](#0-0) 
It never inspects or cleans `self.confirmations` entries that this key added to *other* requests it did not create.

Likewise, in `multisig2/src/lib.rs`, `delete_member` only removes requests proposed by the deleted `member`: [2](#0-1) 
Confirmations the removed member placed on other pending requests are left untouched in the `confirmations: LookupMap<RequestId, HashSet<...>>` map.

`confirm()` in both contracts blindly trusts the size of this stale set: [3](#0-2) [4](#0-3) 
There is no check that every public key / member currently recorded in `confirmations` is still present in the active signer set (`num_requests_pk` keys for `multisig`, or `self.members` for `multisig2`) at the moment the threshold is evaluated. This is analogous to `Consensus.checkSignatures` counting `signatures.length` without deduplicating/validating that each signer is currently authorized — here the "authorized signer" identity check is skipped for entries already inserted before removal.

### Impact Explanation
A malicious or compromised signer, once identified and removed by the remaining members, can still have their earlier confirmation "vote" counted toward executing a pending request they confirmed before removal. This means a request can be executed with strictly fewer *live, currently-trusted* confirmations than `num_confirmations` requires — e.g., threshold 3 satisfied by confirmations from 2 live members plus 1 stale/removed member. This is a multisig request executed below the effective threshold of live signers, matching the Critical impact category "a multisig request executed below threshold," and can lead to unauthorized transfers, key/member additions, or contract upgrades approved with insufficient genuine authorization.

### Likelihood Explanation
This requires no special privilege beyond being (or having been) a legitimate multisig member/key holder — a realistic operational scenario (key rotation, offboarding a member, revoking a compromised key) that multisig owners are expected to perform routinely. Any time a member is removed while a request they previously confirmed is still pending, the stale-vote condition exists; the remaining signers need not even be aware their confirmation math is corrupted.

### Recommendation
When deleting a key/member (`DeleteKey` in `multisig/src/lib.rs`, `DeleteMember` in `multisig2/src/lib.rs`), iterate all pending `requests`/`confirmations` entries (not just ones the removed signer created) and remove that signer's/member's confirmation from every set, or alternatively re-validate at `confirm()` time that every entry in the stored confirmation set still corresponds to a current member/signer before counting it toward `num_confirmations`.

### Proof of Concept
1. Multisig has members `{A, B, C, D}` with `num_confirmations = 3`.
2. `A` calls `add_request(R)` (not self-confirmed).
3. `B` calls `confirm(R)` → `confirmations[R] = {B}`.
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 < 3, not yet executed).
5. Members detect `B`'s key is compromised and remove it via a separate, properly-threshold-confirmed `DeleteMember{member: B}` / `DeleteKey{public_key: B}` request executed by `A, C, D`. This only removes requests *B proposed*; `R` (proposed by `A`) is untouched, and `confirmations[R]` still contains `B`.
6. `members` (or valid signer set) is now `{A, C, D}`.
7. `D` calls `confirm(R)` → `confirmations[R].len() (2) + 1 = 3 >= num_confirmations (3)` → `R` executes, even though only `C` and `D` (2 live members) actually approved it after `B`'s removal, one fewer than the required threshold of 3 live confirmations. [5](#0-4)

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
