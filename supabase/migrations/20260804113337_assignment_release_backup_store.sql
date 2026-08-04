begin;

create schema if not exists oasis_private;
revoke all on schema oasis_private from public, anon, authenticated;
grant usage on schema oasis_private to service_role;

create table if not exists oasis_private.oasis_assignment_release_backups (
    run_id uuid not null
        references public.oasis_backup_runs(id) on delete restrict,
    company_uid text not null,
    prospect_id uuid not null,
    prospect_owner_user_id text not null,
    assignment_snapshot jsonb not null
        check (jsonb_typeof(assignment_snapshot) = 'object'),
    created_at timestamptz not null default now(),
    primary key (run_id, company_uid)
);

alter table oasis_private.oasis_assignment_release_backups
    enable row level security;
alter table oasis_private.oasis_assignment_release_backups
    force row level security;

revoke all on table oasis_private.oasis_assignment_release_backups
    from public, anon, authenticated;
grant select, insert, delete
    on table oasis_private.oasis_assignment_release_backups
    to service_role;

comment on table oasis_private.oasis_assignment_release_backups is
    'Internal reversible snapshots captured before bulk release of personal sales assignments.';

commit;
