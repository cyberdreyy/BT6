## Title
Stale confirmations from removed members count toward quorum on other pending requests - (File: multisig2/src/lib.rs)

## Summary
`delete_member()` only purges cached confirmations for requests that a removed member *authored* (`r.member == member`), not requests that member merely *confirmed*. When such a member is later removed via a separate `DeleteMember` request, their stale entry remains inside `confirmations: LookupMap<RequestId, HashSet<String>>` for any request they confirmed but did not author, and `confirm()`'s threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` counts that stale entry toward quorum. A request can therefore execute with fewer *current* members' approvals than `num_confirmations` requires.

## Finding Description
The invariant that should hold is: **confirmations counted for an executed request == num_confirmations distinct entries drawn from the CURRENT `members` set**. The code breaks this equality.

- `confirm()` at [1](#0-0)  only checks `confirmations.len() as u32 + 1 >= self.num_confirmations`, a pure cardinality check on the cached `HashSet<String>`, with no re-validation of membership of the strings against `self.members`.
- `delete_member()` at [2](#0-1)  removes confirmations only for requests where the deleted member is the *original requester* (`r.member == member`):
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It never scans other pending requests' `confirmations` sets to strip out an entry matching `member.to_string()` when that member merely *confirmed* (but did not author) the request.

Exploit flow with `members = {M1, M2, M3, M4}`, `num_confirmations = 3`:
1. `M2` calls `add_request(R)` (author = M2).
2. `M1` calls `confirm(R)` → `confirmations[R] = {M1}` (len 1, not yet executed since `1+1 < 3`).
3. A separate request `DeleteMember(M1)` is added and confirmed by 3 of the 4 current members (e.g. M1, M3, M4) and executes, calling `delete_member(promise, M1)`. Since `R`'s author is `M2` (not `M1`), the filter in `delete_member` does not touch `confirmations[R]`; `M1`'s stale entry `{M1}` survives inside `confirmations[R]`. `members` is now `{M2, M3, M4}`, still satisfying `members.len() - 1 >= num_confirmations`.
4. `M3` calls `confirm(R)`: `confirmations[R].len() == 1`, `1+1=2 < 3`, so it inserts, giving `confirmations[R] = {M1, M3}`.
5. `M4` calls `confirm(R)`: `confirmations[R].len() == 2`, `2+1=3 >= 3` → `execute_request(R)` fires.

At execution, only `M3` and `M4` — two of the three current members `{M2, M3, M4}` — actually approved `R`; `M1`'s counted confirmation belongs to an account that is no longer a member. No existing guard (`assert_valid_request`, `assert_self_request`, `current_member()`) re-checks the *existing* entries in `confirmations` against the current `members` set; `current_member()` at [3](#0-2)  only validates the caller of the current call, not the previously stored confirmers.

## Impact Explanation
A request (e.g., a `Transfer`, `AddKey`, or `FunctionCall` moving funds or granting access) can execute with strictly fewer approvals from currently-authorized members than `num_confirmations` mandates, because a removed member's stale confirmation is silently counted. This directly matches the enumerated Critical impact "a multisig request executed below `num_confirmations` live members," undermining the core security guarantee of the multisig (k-of-n live authorization) and enabling funds/keys to move with insufficient current authorization. The bug is repeatable for every pending request that was confirmed (but not authored) by any member subsequently removed.

## Likelihood Explanation
This requires normal multisig operation sequencing (no external key compromise): a request pending confirmation, confirmed by a member who is later removed via an independent `DeleteMember` request while the first request is still outstanding, is a foreseeable, low-cost, and repeatable operational sequence rather than a contrived edge case — it can occur during ordinary member turnover (e.g., offboarding a compromised or departing signer) if any of their prior confirmations happen to remain pending.

## Recommendation
In `delete_member()`, iterate over all `confirmations` entries (not just requests authored by the removed member) and strip `member.to_string()` from each `HashSet`, or alternatively re-validate every entry in `confirmations` against `self.members` inside `confirm()` before computing the quorum count (i.e., filter the cached set to intersect with currently valid `members` before checking `len() + 1 >= num_confirmations`).

## Proof of Concept
```rust
#[test]
fn test_stale_confirmation_counts_after_member_removal() {
    // members: M1 (account alice_key AccessKey1), M2, M3, M4 as members via keys/accounts
    // num_confirmations = 3
    // 1. M2 (context) -> add_request(R) targeting some receiver/action
    // 2. M1 (context) -> confirm(R); assert c.confirmations.get(&r_id).unwrap().len() == 1
    // 3. Add + confirm DeleteMember(M1) request using M1, M3, M4 (3 confirmations) -> executes,
    //    removing M1 from members; assert !c.get_members().contains(&M1)
    // 4. assert c.confirmations.get(&r_id).unwrap().contains(&M1.to_string()) // stale entry still present
    // 5. M3 (context) -> confirm(R); assert confirmations.len() == 2 (still not executed)
    // 6. M4 (context) -> confirm(R); this must trigger execute_request()
    // 7. assert c.requests.get(&r_id).is_none() // request executed
    // Binding check: only M3 and M4 (2 of {M2,M3,M4} = 3 live members) approved R,
    // yet it executed as if 3 of 3 live members approved it -> num_confirmations invariant violated.
}
```

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

**File:** multisig2/src/lib.rs (L322-339)
```rust
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
