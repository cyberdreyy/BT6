## Title
Stale confirmations from removed multisig members still count toward the execution threshold, letting a request execute below the live-member quorum — (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

## Summary
The Venus report describes a strict equality check (`repayAmount == borrowBalance`) that becomes stale because the underlying state (`borrowBalance`) changes every block, so the check the transaction relies on no longer matches reality by the time it executes. The analogous binding-breaking pattern in the NEAR multisig contracts (`multisig` and `multisig2`) is that the number of *confirmations counted* for a pending request is not kept in sync with the *live member set*: when a member is removed, its previously recorded confirmations on other, still-pending requests are never purged, so those stale confirmations continue to count toward `num_confirmations` even though that member no longer has any standing in the contract.

## Finding Description
In `multisig2/src/lib.rs`, `confirm()` decides whether to execute a request purely by comparing the size of the stored `confirmations` set to `self.num_confirmations`: [1](#0-0) 

Confirmations are stored keyed only by `request_id`, as a `HashSet<String>` of member identifiers, with no back-reference validation against the current `members` set at confirmation-counting time.

When a member is removed via `delete_member`, the code only purges confirmations for requests where that member was the **originator** (`r.member == member`), not requests where the member had merely **confirmed**: [2](#0-1) 

The same gap exists in the original `multisig` contract: `DeleteKey` removes requests originated by the deleted key (`r.signer_pk == pk`) but never scans other requests' `confirmations` maps to strip that key's prior confirmation: [3](#0-2) [4](#0-3) 

So the binding that should hold is:
```
confirmations_counted(request) == confirmations_from_current_live_members(request)
```
But because removal only prunes requests the departing member *created*, not confirmations it *gave* on requests it did not create, the actual state is:
```
confirmations_counted(request) = confirmations_from_current_live_members(request) + confirmations_from_removed_members(request)
```
This lets a request reach `num_confirmations` and execute (`execute_request`) with fewer *live* member approvals than the configured threshold requires.

## Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A pending request (e.g., a `Transfer`, `AddKey`/`AddMember`, or `FunctionCall` action) can be executed with assent from fewer currently-authorized members than `num_confirmations`, because a stale confirmation from an already-removed member is still counted. This can allow funds to move or privileges to be granted (adding a new full-access key, adding a new member) with less real consensus than the multisig was configured to require, undermining the entire custody guarantee of the multisig scheme.

## Likelihood Explanation
This requires only the normal course of multisig operation: a member confirms a request that is not yet fully confirmed, and is later removed from the multisig (a routine, expected lifecycle event such as offboarding a signer or rotating keys) before that earlier request is confirmed/executed or deleted. No malicious actor needs special access beyond being (or having been) a legitimate multisig member — the flaw is in the contract's own bookkeeping, not in any external trust assumption. Given that `delete_request` requires waiting `REQUEST_COOLDOWN` and members do not always clean up all outstanding requests before removing a co-member, stale-but-live requests carrying a departed member's confirmation are a realistic and easily triggered condition.

## Recommendation
When removing a member/key (`delete_member` / `DeleteKey`), iterate over **all** pending requests' confirmation sets (not just those the member originated) and remove that member's confirmation entry from each. Alternatively, at confirmation-counting time in `confirm()`, recompute the effective confirmation count by intersecting the stored `confirmations` set with the *current* `members` set (or public key list) rather than trusting the raw stored set size.

## Proof of Concept
1. Deploy `multisig2` with `num_confirmations = 3` and members `{A, B, C, D}`.
2. `A` calls `add_request` to create request `R` (e.g., a `Transfer`), then `confirm(R)` — confirmations for `R` = `{A}`.
3. Separately, members `B`, `C`, `D` submit and confirm a request to remove `A` via `DeleteMember { member: A }`; `delete_member` executes, removing `A` from `members`, but only purges requests *originated* by `A` — `R` (originated by `A` itself, but assume instead `B` originated `R` and `A` merely confirmed it to make the point cleaner) is untouched because `r.member != A`.
4. `A` is now fully removed from the multisig and holds no access key/authority.
5. `B` and `C` call `confirm(R)`. `confirmations.len()` becomes `{A, B}` then `{A, B, C}` → size 3 ≥ `num_confirmations` (3), and `execute_request(R)` runs — even though only `B` and `C` are actually live, authorized members who approved, one confirmation short of the configured 3-of-N threshold among current members. [1](#0-0) [2](#0-1)

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
