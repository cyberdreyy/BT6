## Finding

### Title
Stale confirmations from removed multisig members/keys are not purged, allowing a request to execute below the effective live-member threshold - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`confirm()` compares the *size* of the stored `confirmations` set against `num_confirmations` without verifying that every entry in that set still corresponds to a current, live multisig member/key. `delete_member` (and the analogous `DeleteKey` action in the older `multisig` contract) only purges confirmations for requests that were *created by* the removed member — it does not purge confirmations that removed member previously *added* to requests created by someone else. A stale confirmation therefore keeps counting toward the threshold even after that signer has been revoked, letting a request execute with fewer currently-authorized approvals than `num_confirmations` requires.

### Finding Description
The reported bug (`utilizationRate()` failing to guard against `reserves > cash`) is a case of a computed value being treated as bounded/consistent when the underlying invariant (reserves ≤ cash) is not actually enforced. The equivalent invariant that this codebase must hold is:

```
confirmations.len() (for a pending request) == number of currently-authorized members who approved it
```

In `multisig2/src/lib.rs`:

```rust
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
    let request_ids: Vec<u32> = self.requests.iter()
        .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    ...
    self.members.remove(&member);
``` [1](#0-0) 

This only clears confirmations for requests the removed member *authored* (`r.member == member`). Requests authored by other members that the removed member had previously *confirmed* are left untouched — their confirmation string remains inside the `confirmations: LookupMap<RequestId, HashSet<String>>` entry.

Then `confirm()` decides whether to execute purely by set size:

```rust
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    self.assert_valid_request(request_id);
    let member = self.current_member().unwrap_or_else(...);
    let mut confirmations = self.confirmations.get(&request_id).unwrap();
    assert(!confirmations.contains(&member.to_string()), ...);
    if confirmations.len() as u32 + 1 >= self.num_confirmations {
        let request = self.remove_request(request_id);
        self.execute_request(request)
    } else {
        confirmations.insert(member.to_string());
        ...
    }
}
``` [2](#0-1) 

`assert_valid_request` only checks that the *caller* confirming right now is a current member — it never re-validates the other entries already stored in `confirmations`:

```rust
fn assert_valid_request(&mut self, request_id: RequestId) {
    assert(self.current_member().is_some(), ...);
    assert(self.requests.get(&request_id).is_some(), ...);
    assert(self.confirmations.get(&request_id).is_some(), ...);
}
``` [3](#0-2) 

The identical pattern exists in the original `multisig` contract's `DeleteKey` handling, which also only removes requests where `r.signer_pk == pk`, leaving stale confirmations by that key on other members' requests: [4](#0-3) .

### Impact Explanation
This breaks the custody binding "confirmations counted vs. live members" and falls under the Critical category "a multisig request executed below threshold." A request (e.g., `Transfer`, `AddKey`, `FunctionCall` moving funds) can execute with fewer genuinely authorized approvals than `num_confirmations`, because one or more of the counted approvals belongs to a member/key that has since been revoked (e.g., an offboarded employee, a compromised key that was proactively removed, or a member removed as part of routine key rotation). This directly undermines the security guarantee of the k-of-n scheme and can allow unauthorized movement of NEAR held by the multisig account.

### Likelihood Explanation
The scenario requires no special privilege beyond normal multisig operation: any member can create a request and have it confirmed by another member before that other member is later removed (a routine operational event — key rotation, offboarding). The moment the remaining live members supply the last confirmation, the stale confirmation is silently counted, and the request executes. No malicious cooperation, foundation intervention, or code-path outside the documented `add_request` / `confirm` / `DeleteMember` flow is needed.

### Recommendation
When counting confirmations in `confirm()` (and anywhere `num_confirmations` is checked), filter the confirmation set to only entries that are still present in `self.members` before comparing against `num_confirmations`. Alternatively, when a member/key is removed via `delete_member`/`DeleteKey`, iterate all requests (not just ones authored by that member) and strip the removed identity from every stored confirmation set.

### Proof of Concept
1. Deploy `multisig2` with 4 members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R` (e.g., `Transfer` to some address), `member = A`.
3. `D` calls `confirm(R)` → `confirmations = {D}` (len 1, `1+1=2 < 3`, not executed).
4. Members legitimately vote to remove `D` (e.g., due to suspected key compromise) via a separate `DeleteMember { member: D }` request that reaches 3 confirmations and executes. Post-condition: `members.len() = 3 == num_confirmations`, satisfying the `delete_member` assert; `D`'s stale confirmation on `R` is **not** removed because `r.member == A`, not `D`.
5. `A` calls `confirm(R)` → `confirmations = {D, A}` (len 2, `2+1=3 >= 3` — wait, this already triggers on this call).
   - To make the third confirmer distinct: instead skip step 5 and have `B` call `confirm(R)` directly: `confirmations.len() == 1` (`{D}`), `1+1 = 2 < 3`; then `A` calls `confirm(R)`: `confirmations.len() == 2` (`{D, B}`), `2+1 = 3 >= 3` → `execute_request(R)` runs.
6. `R` executes (e.g., transfers NEAR) having only `B` and `A` as genuinely live-authorized confirmers plus the stale `D` confirmation — i.e., the request executed with 2 live confirmations against a nominal 3-of-4 threshold. [5](#0-4) [6](#0-5)

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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
