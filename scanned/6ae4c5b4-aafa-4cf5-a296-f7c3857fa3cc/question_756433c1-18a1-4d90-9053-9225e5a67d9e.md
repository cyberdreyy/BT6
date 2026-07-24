[File: 'crates/sui-core/src/authority/authority_store_types.rs -> Scope: Critical. Unauthorized creation, duplication, transfer, release, withdrawal, destruction bypass, or custody escape of SUI, bridged assets, objects, or package-controlled value through verifier, runtime, bridge, ownership, or settlement failure'] [Symbol: try_construct_object / ObjectInner construction] Can an attacker-controlled StoreObjectValue with StoreData::Coin(balance) and Owner::ObjectOwner(parent_id) reach try_construct_object and violate the invariant that

```python
questions = [
