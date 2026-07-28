-- 도 단위 조회는 district가 비어 있으므로 별도 인덱스로 정렬 비용을 제거한다.
create index if not exists idx_oasis_employment_contacts_province_growth
    on public.oasis_employment_contacts (
        province_code,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0;

create index if not exists idx_oasis_employment_contacts_province_industry
    on public.oasis_employment_contacts (
        province_code,
        industry_category,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0;

create index if not exists idx_oasis_employment_contacts_mobile_province
    on public.oasis_employment_contacts (
        province_code,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_mobile_phone;

create index if not exists idx_oasis_employment_contacts_landline_province
    on public.oasis_employment_contacts (
        province_code,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_landline_phone;

create index if not exists idx_oasis_employment_contacts_email_province
    on public.oasis_employment_contacts (
        province_code,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_email;

create index if not exists idx_oasis_employment_contacts_instagram_province
    on public.oasis_employment_contacts (
        province_code,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_instagram;
