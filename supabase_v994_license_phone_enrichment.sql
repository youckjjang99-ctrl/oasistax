alter table public.oasis_licensed_businesses
    add column if not exists phone_source text not null default '';
alter table public.oasis_licensed_businesses
    add column if not exists phone_source_url text not null default '';
alter table public.oasis_licensed_businesses
    add column if not exists phone_confidence integer not null default 0;
alter table public.oasis_licensed_businesses
    add column if not exists phone_enrichment_status text not null default 'pending';
alter table public.oasis_licensed_businesses
    add column if not exists phone_checked_at timestamptz;
alter table public.oasis_licensed_businesses
    add column if not exists phone_enrichment_error text not null default '';

create index if not exists oasis_licensed_businesses_phone_enrichment_idx
    on public.oasis_licensed_businesses (
        phone_enrichment_status,
        phone_checked_at,
        created_at
    )
    where phone = '';

create or replace function public.oasis_preserve_enriched_phone()
returns trigger
language plpgsql
as $$
begin
    if old.phone <> '' and coalesce(new.phone, '') = '' then
        new.phone := old.phone;
        new.phone_source := old.phone_source;
        new.phone_source_url := old.phone_source_url;
        new.phone_confidence := old.phone_confidence;
        new.phone_enrichment_status := old.phone_enrichment_status;
        new.phone_checked_at := old.phone_checked_at;
        new.phone_enrichment_error := old.phone_enrichment_error;
    end if;
    return new;
end;
$$;

drop trigger if exists oasis_preserve_enriched_phone_trigger
    on public.oasis_licensed_businesses;
create trigger oasis_preserve_enriched_phone_trigger
before update on public.oasis_licensed_businesses
for each row execute function public.oasis_preserve_enriched_phone();
