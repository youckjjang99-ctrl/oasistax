-- OASIS CRM v10.2.5 - 경정청구 수집자료 비공개 다운로드 형식 허용
-- 기존 XML 호환성을 유지하면서 PDF·JSON·엑셀·CSV 수집파일을 허용한다.

begin;

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'oasis-claim-documents',
    'oasis-claim-documents',
    false,
    20971520,
    array[
        'application/pdf',
        'application/json',
        'application/xml',
        'text/xml',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
        'text/csv'
    ]::text[]
)
on conflict (id) do update
set
    name = excluded.name,
    public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

commit;
