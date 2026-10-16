"""Build source-only v9.14.4 overlay using the existing audited ZIP builder."""
import build_ai_calling_admin_patch as builder

builder.BASE = "bbd424a18be06e9829c9cd9e3a930b4f4ffd4e98"
builder.VERSION = "v9.14.4-ai-visit-calling-simple-launch"
builder.MANIFEST = "docs/ai-visit-calling-simple-files-v9.14.4.md"

if __name__ == "__main__":
    raise SystemExit(builder.main())
