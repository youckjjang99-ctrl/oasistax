import inspect

from claim_correction_repository import ClaimRepository


def test_claim_document_store_never_physically_deletes_customer_objects():
    source = inspect.getsource(ClaimRepository.store_collected_document)

    assert "delete_private_object" not in source
    assert "Previous versions are intentionally retained" in source
