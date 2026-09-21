"""Source-only v9.13.3 overlay; reuse the reviewed guarded ZIP builder."""
from __future__ import annotations

import build_crm_consistency_patch as builder


builder.BASE = "ebfdf365ab6b95ce5c7cdb74306777af199a860e"
builder.VERSION = "v9.13.3-cretop-registration-identity-contact"
builder.MANIFEST = "docs/cretop-registration-files-v9.13.3.md"


if __name__ == "__main__":
    raise SystemExit(builder.main())
