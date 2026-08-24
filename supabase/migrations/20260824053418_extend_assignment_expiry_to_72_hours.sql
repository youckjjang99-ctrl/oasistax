begin;

alter table public.oasis_sales_assignment_settings
    alter column assignment_hours set default 72;

update public.oasis_sales_assignment_settings
set
    assignment_hours = 72,
    updated_at = now()
where assignment_hours <> 72;

-- Preserve every active assignment and extend only untouched temporary rows.
update public.oasis_company_sales_assignments
set
    assignment_expires_at = greatest(
        assignment_expires_at,
        assigned_at + interval '72 hours'
    ),
    updated_at = now()
where status in ('assigned', 'pending_contact')
  and assigned_user_id is not null
  and assigned_at is not null
  and assignment_expires_at is not null
  and current_assignment_contact_count = 0
  and current_assignment_first_contacted_at is null
  and legacy_hold is false
  and permanently_excluded is false
  and migration_conflict is false;

commit;
