alter table public.oasis_claim_sales_applications
    add column if not exists management_homepage_url text not null default '',
    add column if not exists sales_code text not null default '',
    add column if not exists sales_homepage_url text not null default '';

alter table public.oasis_claim_sales_applications
    drop constraint if exists oasis_claim_sales_management_url_length,
    add constraint oasis_claim_sales_management_url_length
        check (length(management_homepage_url) <= 2048) not valid,
    drop constraint if exists oasis_claim_sales_code_length,
    add constraint oasis_claim_sales_code_length
        check (length(sales_code) <= 80) not valid,
    drop constraint if exists oasis_claim_sales_homepage_url_length,
    add constraint oasis_claim_sales_homepage_url_length
        check (length(sales_homepage_url) <= 2048) not valid;

alter table public.oasis_claim_sales_applications
    validate constraint oasis_claim_sales_management_url_length;
alter table public.oasis_claim_sales_applications
    validate constraint oasis_claim_sales_code_length;
alter table public.oasis_claim_sales_applications
    validate constraint oasis_claim_sales_homepage_url_length;
