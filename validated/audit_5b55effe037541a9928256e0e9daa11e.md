## Finding

### Title
Removed multisig members' confirmations are not purged from other pending requests, allowing a request to execute with confirmations from fewer than the configured threshold of live members - (File: `multisig2/src/lib.rs`, also affects `multisig/src/lib.rs`)

### Summary
`delete_member` (and the legacy `DeleteKey` action) only removes requests that the removed member itself created; it never scrubs that member's confirmation entries from requests created by *other* members. Once a member/key is removed, its stale confirmation stays in the `confirmations` set for any request it had previously confirmed. `confirm()` only compares the size of that stored set to `self.num_confirmations`, so a request can later be pushed over the threshold and executed even though the number of confirmations coming from currently-valid members is strictly less than `num_confirmations`.

### Finding Description
The confirmation-counting invariant the contract is supposed to enforce is:

```
count(confirmations from members that are still valid) >= num_confirmations
```

but the implementation actually enforces:

```
count(confirmations stored, regardless of current membership) >= num_confirmations
```

In `confirm()`: [1](#0-0) 

the size check `confirmations.len() as u32 + 1 >= self.num_confirmations` is performed against whatever public keys/accounts happen to be stored in `self.confirmations.get(&request_id)`, with no cross-check against `self.members`.

`delete_member` only cleans up requests *added by* the removed member, not confirmations that member left on requests added by others: [2](#0-1) 

The same gap exists in the legacy single-file `multisig` contract's `DeleteKey` handling, which likewise only purges requests signed by the deleted key, not confirmations that key gave on requests created by other keys: [3](#0-2) 

So once a member is removed, any confirmation they previously cast on a request created by someone else remains counted forever, silently lowering the effective quorum for that specific request below the configured `num_confirmations`.

### Impact Explanation
This breaks the core custody/authorization binding of the multisig: the number of *live* signers agreeing to move funds or execute privileged actions (`Transfer`, `FunctionCall`, `AddKey`, `AddMember`/`DeleteMember`, `DeployContract`, etc.) can be strictly less than the configured threshold `num_confirmations`, while the contract still executes the request. This is a "multisig request executed below threshold" condition, listed as Critical impact, since it lets a request pass with insufficient live authorization (e.g., a transfer approved by effectively 2 live members while `num_confirmations = 3`).

### Likelihood Explanation
This requires only the normal, documented multisig lifecycle: a member confirms a request that isn't immediately executed (partial confirmation), that member is later removed via a legitimate `DeleteMember`/`DeleteKey` request (e.g. routine key rotation or offboarding — an expected, supported operation, not a compromise of the foundation or an owner privilege), and then remaining members continue confirming the still-pending request. No malicious validator, redeploy, victim key theft, or social engineering is required — it is a straightforward consequence of normal multisig operations plus the missing cleanup step, so likelihood is high whenever member turnover happens while requests are outstanding.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, and the `DeleteKey` action in `multisig/src/lib.rs`), iterate over all pending requests' confirmation sets (not just requests the member created) and remove the departing member's/key's confirmation entry from each of them, decrementing effective counts accordingly. Alternatively, at `confirm()` time, validate that every stored confirmation entry still corresponds to a current member of `self.members` before comparing the count to `self.num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 4 members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `A` calls `add_request` to create request `R` (e.g. a `Transfer`).
3. Member `D` calls `confirm(R)` → `confirmations = {A, D}` (len 2, count via `add_request_and_confirm`/`confirm` per `multisig2/src/lib.rs:292-315`).
4. Members confirm a separate `DeleteMember { member: D }` request and execute it via `delete_member` (`multisig2/src/lib.rs:355-379`); this only checks/removes requests created by `D` — `R` (created by `A`) is untouched, so `R`'s confirmation set still contains `D`.
5. Member `C` calls `confirm(R)`. `confirmations.len() + 1 = 3 >= num_confirmations (3)` at `multisig2/src/lib.rs:304`, so `R` executes — even though only `A` and `C` are still live members who confirmed it (2 live confirmations), plus the stale confirmation from removed member `D`. [1](#0-0) [2](#0-1)

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
